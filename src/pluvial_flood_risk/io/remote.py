"""OGC API Features download with parallel paging, SSRF protection, and FlatGeobuf support."""

import asyncio
import logging
import os
import tempfile
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp
import geopandas as gpd
from fastprocesses.core.exceptions import SSRFBlockedError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

_MAX_RETRY_ATTEMPTS = 10


def _is_retryable(exc: BaseException) -> bool:
    """Return True for exceptions that warrant a retry.

    Retries on:
    - ``asyncio.TimeoutError`` — server too slow, often transient.
    - ``aiohttp.ClientResponseError`` with status 429 or 5xx — rate-limit or
      temporary server error.
    - Any other ``aiohttp.ClientError`` (connection reset, DNS failure, etc.).

    Does **not** retry on 4xx client errors (bad request, auth, not found).
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status == 429 or exc.status >= 500
    if isinstance(exc, aiohttp.ClientError):
        return True
    return False


def _before_sleep_log(retry_state: RetryCallState) -> None:
    """Log a WARNING before each retry, including the URL and attempt count."""
    url = retry_state.args[0] if retry_state.args else "unknown"
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Retrying request to %s (attempt %d/%d) after: %s",
        url, retry_state.attempt_number, _MAX_RETRY_ATTEMPTS, exc,
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(_MAX_RETRY_ATTEMPTS),
    before_sleep=_before_sleep_log,
    reraise=True,
)
async def _get_json(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Fetch JSON from *url* via GET, respecting *semaphore*.

    Raises native aiohttp / asyncio exceptions so the tenacity retry
    predicate can inspect them.  Context-enriched re-raises happen in
    the callers after all retries are exhausted.
    """
    async with semaphore:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential_jitter(initial=1, max=60),
    stop=stop_after_attempt(_MAX_RETRY_ATTEMPTS),
    before_sleep=_before_sleep_log,
    reraise=True,
)
async def _get_bytes(
    url: str,
    session: aiohttp.ClientSession,
) -> bytes:
    """Fetch raw bytes from *url* via GET.

    Same retry / exception semantics as :func:`_get_json`.
    """
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.read()


def fetch_ogc_features(
    url: str,
    *,
    allowed_origins: list[str] | None = None,
    page_limit: int = 1000,
    timeout: float = 30.0,
    max_concurrent_requests: int = 10,
) -> dict:
    """Fetch a complete GeoJSON FeatureCollection from an OGC API Features
    items endpoint.

    Uses the OGC collections endpoint to detect itemCount and performs parallel
    paging via offset/limit. Falls back to sequential next-link following if
    itemCount is unavailable. Both application/geo+json and application/flatgeobuf
    responses are decoded to in-memory GeoJSON FeatureCollection.

    SSRF-checks every hop when allowed_origins is supplied (for user-supplied URLs).
    Omit allowed_origins for server-side URLs (no SSRF exposure).

    Parameters
    ----------
    url : str
        OGC API Features items endpoint URL
        (e.g. https://example.com/collections/buildings/items).
    allowed_origins : list[str] | None
        List of allowed origin prefixes (e.g. ['https://example.com/']).
        If None, no SSRF check is performed (safe for server-side URLs).
    page_limit : int
        Number of items per page. Default 1000.
    timeout : float
        Request timeout in seconds. Default 30.
    max_concurrent_requests : int
        Maximum number of concurrent HTTP requests. Default 10.
        Prevents request storms when paging large datasets.

    Returns
    -------
    dict
        GeoJSON FeatureCollection dict with all features merged.

    Raises
    ------
    SSRFBlockedError
        If URL origin is not in allowed_origins.
    ValueError
        If download, parsing, or validation fails.
    """
    logger.info(
        "fetch_ogc_features: url=%s page_limit=%d max_concurrent=%d",
        url, page_limit, max_concurrent_requests,
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def _run() -> dict:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            return await _fetch_ogc_features_async(
                url,
                session=session,
                allowed_origins=allowed_origins,
                page_limit=page_limit,
                semaphore=semaphore,
            )

    return asyncio.run(_run())


async def _fetch_ogc_features_async(
    url: str,
    *,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
    page_limit: int = 1000,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Async implementation of fetch_ogc_features.

    Attempts parallel paging via itemCount from collections endpoint.
    Falls back to sequential next-link following if unavailable.
    """
    if allowed_origins is not None:
        _assert_allowed(url, allowed_origins)

    item_count = await _get_item_count(url, session, allowed_origins)

    if item_count is not None:
        n_pages = -(-item_count // page_limit)  # ceiling division
        logger.info(
            "Parallel paging: %d features across %d pages (limit=%d)",
            item_count, n_pages, page_limit,
        )
        features = await _fetch_pages_parallel(
            url, item_count, page_limit, session, allowed_origins, semaphore
        )
    else:
        logger.info(
            "itemCount unavailable; falling back to sequential next-link paging"
        )
        features = await _fetch_pages_sequential(
            url, page_limit, session, allowed_origins, semaphore
        )

    logger.info("fetch_ogc_features: fetched %d features total from %s", len(features), url)
    return {"type": "FeatureCollection", "features": features}


async def _get_item_count(
    url: str,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
) -> int | None:
    """Try to extract itemCount from the OGC collections endpoint.

    Converts items endpoint to collections endpoint by removing the 'items'
    path segment, then GETs the collection metadata to read itemCount.

    Returns None if the conversion fails or the endpoint doesn't advertise
    itemCount (callers should fall back to next-link following).
    """
    parsed = urlparse(url)
    path_parts = parsed.path.rstrip("/").split("/")

    # Check if the URL ends with 'items'
    if path_parts[-1] != "items" or len(path_parts) < 2:
        return None

    # Reconstruct collections URL: drop /items path segment and strip all
    # query parameters except 'f' (format) — extra params like filters are
    # not valid on the collections endpoint and cause 400 responses.
    collections_path = "/".join(path_parts[:-1])
    original_query = parse_qs(parsed.query, keep_blank_values=True)
    collections_query = {k: v for k, v in original_query.items() if k == "f"}
    collections_url = urlunparse(
        parsed._replace(
            path=collections_path,
            query=urlencode(collections_query, doseq=True),
        )
    )

    try:
        if allowed_origins is not None:
            _assert_allowed(collections_url, allowed_origins)

        logger.debug("Querying collections endpoint for itemCount: %s", collections_url)
        async with session.get(collections_url) as response:
            response.raise_for_status()
            data = await response.json()

            # Collection object should have itemCount at root level
            if isinstance(data, dict) and "itemCount" in data:
                count = data["itemCount"]
                logger.debug("itemCount=%d from %s", count, collections_url)
                return count

            logger.debug(
                "No itemCount in collection response from %s", collections_url
            )
            return None
    except Exception as exc:
        # Collections endpoint unavailable or unparseable; fall back to next-link
        logger.debug("Could not fetch itemCount from %s: %s", collections_url, exc)
        return None


async def _fetch_pages_parallel(
    url: str,
    item_count: int,
    page_limit: int,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetch all pages in parallel using offset/limit parameters."""
    offsets = list(range(0, item_count, page_limit))

    tasks = [
        _fetch_page_with_offset(
            url, offset, page_limit, session,
            allowed_origins, semaphore
        )
        for offset in offsets
    ]

    page_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect errors and merge features
    features = []
    for result in page_results:
        if isinstance(result, BaseException):
            raise result
        
        features.extend(result.get("features", []))

    return features


async def _fetch_page_with_offset(
    url: str,
    offset: int,
    limit: int,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Fetch a single page using offset and limit parameters."""
    page_url = _with_limit(_with_offset(url, offset), limit)

    if allowed_origins is not None:
        _assert_allowed(page_url, allowed_origins)

    logger.debug(
        "Fetching page offset=%d limit=%d (URL: %s)", offset, limit, url
    )
    try:
        return await _get_json(page_url, session, semaphore or asyncio.Semaphore(1))
    except asyncio.TimeoutError as exc:
        logger.error(
            "Timeout (all %d retries exhausted) fetching page offset=%d from %s",
            _MAX_RETRY_ATTEMPTS, offset, page_url,
        )
        raise asyncio.TimeoutError(
            f"Timeout fetching page offset={offset} from {page_url!r}"
        ) from exc
    except aiohttp.ClientResponseError as exc:
        logger.error(
            "HTTP %d fetching page offset=%d from %s",
            exc.status, offset, page_url,
        )
        raise ValueError(
            f"HTTP {exc.status} fetching page offset={offset} from {page_url!r}: "
            f"{exc.message}"
        ) from exc
    except aiohttp.ClientError as exc:
        logger.error(
            "Network error (all %d retries exhausted) fetching page offset=%d from %s: %s",
            _MAX_RETRY_ATTEMPTS, offset, page_url, exc,
        )
        raise ValueError(
            f"Network error fetching page offset={offset} from {page_url!r}: {exc}"
        ) from exc


async def _fetch_pages_sequential(
    url: str,
    page_limit: int,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetch pages sequentially following links[rel=next]."""
    features = []
    next_url: str | None = _with_limit(url, page_limit)
    page_num = 0

    while next_url:
        if allowed_origins is not None:
            _assert_allowed(next_url, allowed_origins)

        logger.debug("Fetching sequential page %d: %s", page_num, next_url)
        try:
            fc = await _get_json(next_url, session, semaphore or asyncio.Semaphore(1))
        except asyncio.TimeoutError as exc:
            logger.error(
                "Timeout (all %d retries exhausted) fetching sequential page %d from %s",
                _MAX_RETRY_ATTEMPTS, page_num, next_url,
            )
            raise asyncio.TimeoutError(
                f"Timeout fetching sequential page {page_num} from {next_url!r}"
            ) from exc
        except aiohttp.ClientResponseError as exc:
            logger.error(
                "HTTP %d fetching sequential page %d from %s",
                exc.status, page_num, next_url,
            )
            raise ValueError(
                f"HTTP {exc.status} fetching sequential page {page_num} "
                f"from {next_url!r}: {exc.message}"
            ) from exc
        except aiohttp.ClientError as exc:
            logger.error(
                "Network error (all %d retries exhausted) on sequential page %d from %s: %s",
                _MAX_RETRY_ATTEMPTS, page_num, next_url, exc,
            )
            raise ValueError(
                f"Network error on sequential page {page_num} from {next_url!r}: {exc}"
            ) from exc

        page_features = fc.get("features", [])
        features.extend(page_features)
        logger.debug("Page %d: %d features (running total: %d)", page_num, len(page_features), len(features))
        next_url = _next_link(fc)
        page_num += 1

    return features


def decode_flatgeobuf_bytes(data: bytes) -> dict:
    """Decode FlatGeobuf bytes into a GeoJSON FeatureCollection dict.

    Uses geopandas.read_file (NOT flatgeobuf.load) to avoid crashes on
    paged FlatGeobuf responses that carry spatial indexes. The geopandas
    driver correctly handles all FlatGeobuf variants.

    Parameters
    ----------
    data : bytes
        Raw FlatGeobuf binary payload.

    Returns
    -------
    dict
        GeoJSON FeatureCollection dict.

    Raises
    ------
    ValueError
        If decoding fails.
    """
    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(data)

    try:
        gdf = gpd.read_file(tmp_path)
        return {
            "type": "FeatureCollection",
            "features": list(gdf.iterfeatures()),
        }
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def fetch_flatgeobuf(
    url: str,
    *,
    allowed_origins: list[str] | None = None,
    timeout: float = 30.0,
) -> dict:
    """Fetch a FlatGeobuf resource and decode it to a GeoJSON FeatureCollection.

    Use this when the client supplies a ``{href, type}`` reference object with
    ``type='application/flatgeobuf'``.  The raw bytes are fetched in a single
    request and decoded via :func:`decode_flatgeobuf_bytes`.

    Parameters
    ----------
    url : str
        Direct URL to a FlatGeobuf file or endpoint.
    allowed_origins : list[str] | None
        SSRF allowlist; same semantics as :func:`fetch_ogc_features`.
    timeout : float
        Request timeout in seconds. Default 30.

    Returns
    -------
    dict
        GeoJSON FeatureCollection dict.

    Raises
    ------
    SSRFBlockedError
        If the URL origin is not in *allowed_origins*.
    ValueError
        If the download or FlatGeobuf decoding fails.
    """
    logger.info("fetch_flatgeobuf: url=%s", url)
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async def _run() -> dict:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            return await _fetch_flatgeobuf_async(url, session=session, allowed_origins=allowed_origins)

    return asyncio.run(_run())


async def _fetch_flatgeobuf_async(
    url: str,
    *,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None = None,
) -> dict:
    """Async implementation of fetch_flatgeobuf."""
    if allowed_origins is not None:
        _assert_allowed(url, allowed_origins)

    try:
        data = await _get_bytes(url, session)
    except asyncio.TimeoutError as exc:
        logger.error(
            "Timeout (all %d retries exhausted) fetching FlatGeobuf from %s",
            _MAX_RETRY_ATTEMPTS, url,
        )
        raise asyncio.TimeoutError(
            f"Timeout fetching FlatGeobuf from {url!r}"
        ) from exc
    except aiohttp.ClientResponseError as exc:
        logger.error("HTTP %d fetching FlatGeobuf from %s", exc.status, url)
        raise ValueError(
            f"HTTP {exc.status} fetching FlatGeobuf from {url!r}: {exc.message}"
        ) from exc
    except aiohttp.ClientError as exc:
        logger.error(
            "Network error (all %d retries exhausted) fetching FlatGeobuf from %s: %s",
            _MAX_RETRY_ATTEMPTS, url, exc,
        )
        raise ValueError(
            f"Network error fetching FlatGeobuf from {url!r}: {exc}"
        ) from exc

    logger.info("fetch_flatgeobuf: received %d bytes from %s", len(data), url)
    result = decode_flatgeobuf_bytes(data)
    logger.info("fetch_flatgeobuf: decoded %d features", len(result.get("features", [])))
    return result


def fetch_all_references(
    references: dict[str, dict],
    *,
    allowed_origins: list[str] | None = None,
    timeout: float = 30.0,
    page_limit: int = 10000,
    max_concurrent_requests: int = 10,
) -> dict[str, dict]:
    """Fetch all ``{href, type}`` reference objects concurrently.

    Creates one :class:`aiohttp.ClientSession` per distinct server domain and
    runs all downloads in a single event loop, so connection pools are reused
    across pages of the same server.

    Parameters
    ----------
    references:
        Mapping of input key → ``{href, type}`` reference object
        (e.g. ``{"buildings": {"href": "https://…", "type": "application/geo+json"}}``.
    allowed_origins:
        SSRF allowlist; same semantics as :func:`fetch_ogc_features`.
    timeout:
        Request timeout in seconds applied to every session. Default 30.
    page_limit:
        Items per page for OGC Features paging. Default 1000.
    max_concurrent_requests:
        Global semaphore shared across all sessions. Default 10.

    Returns
    -------
    dict[str, dict]
        Same keys as *references*, values are resolved GeoJSON FeatureCollection dicts.
    """
    return asyncio.run(
        _fetch_all_references_async(
            references,
            allowed_origins=allowed_origins,
            timeout=timeout,
            page_limit=page_limit,
            max_concurrent_requests=max_concurrent_requests,
        )
    )


def _domain(href: str) -> str:
    """Return scheme://netloc for grouping URLs by server."""
    parsed = urlparse(href)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _fetch_reference_async(
    ref: dict,
    *,
    session: aiohttp.ClientSession,
    allowed_origins: list[str] | None,
    page_limit: int,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Dispatch a single ``{href, type}`` reference to the appropriate fetcher."""
    href: str = ref["href"]
    media_type: str = ref["type"]
    if media_type == "application/flatgeobuf":
        return await _fetch_flatgeobuf_async(
            href, session=session, allowed_origins=allowed_origins
        )
    return await _fetch_ogc_features_async(
        href,
        session=session,
        allowed_origins=allowed_origins,
        page_limit=page_limit,
        semaphore=semaphore,
    )


async def _fetch_all_references_async(
    references: dict[str, dict],
    *,
    allowed_origins: list[str] | None,
    timeout: float,
    page_limit: int,
    max_concurrent_requests: int,
) -> dict[str, dict]:
    """Fetch all references concurrently, one ClientSession per domain."""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    domains = {_domain(ref["href"]) for ref in references.values()}
    sessions: dict[str, aiohttp.ClientSession] = {
        domain: aiohttp.ClientSession(timeout=client_timeout)
        for domain in domains
    }

    try:
        coros = {
            key: _fetch_reference_async(
                ref,
                session=sessions[_domain(ref["href"])],
                allowed_origins=allowed_origins,
                page_limit=page_limit,
                semaphore=semaphore,
            )
            for key, ref in references.items()
        }
        results_list = await asyncio.gather(*coros.values(), return_exceptions=True)
        resolved: dict[str, dict] = {}
        for key, result in zip(coros.keys(), results_list):
            if isinstance(result, BaseException):
                raise result
            resolved[key] = result  # type: ignore[assignment]
        return resolved
    finally:
        await asyncio.gather(*(s.close() for s in sessions.values()))


def _assert_allowed(url: str, allowed_origins: list[str]) -> None:
    """Raise SSRFBlockedError if the URL origin is not in the allowlist.

    Parameters
    ----------
    url : str
        URL to check.
    allowed_origins : list[str]
        List of allowed origin prefixes (e.g. ['https://example.com/']).

    Raises
    ------
    SSRFBlockedError
        If URL origin does not match any allowed prefix.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"

    if not any(origin.startswith(prefix) for prefix in allowed_origins):
        raise SSRFBlockedError(
            f"URL origin '{origin}' is not in the allowed list: {allowed_origins}"
        )


def _with_offset(url: str, offset: int) -> str:
    """Add or replace the 'offset' query parameter (OGC API Features)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["offset"] = [str(offset)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _with_limit(url: str, limit: int) -> str:
    """Add or replace the 'limit' query parameter (OGC API Features)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["limit"] = [str(limit)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _next_link(fc: dict) -> str | None:
    """Extract rel=next URL from a GeoJSON FeatureCollection's links array.

    Parameters
    ----------
    fc : dict
        GeoJSON FeatureCollection object.

    Returns
    -------
    str | None
        URL of the next page, or None if absent.
    """
    for link in fc.get("links", []):
        if link.get("rel") == "next":
            return link.get("href")
    return None
