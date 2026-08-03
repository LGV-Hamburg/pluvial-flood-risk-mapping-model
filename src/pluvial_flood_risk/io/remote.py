"""OGC API Features download with parallel paging, SSRF protection, and FlatGeobuf support."""

import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from itertools import cycle
from typing import NoReturn, cast
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

import joblib

from ..settings import settings

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


@asynccontextmanager
async def _open_sessions(n: int, timeout: aiohttp.ClientTimeout):
    """Async context manager yielding a pool of *n* independent ClientSessions."""
    sessions = [aiohttp.ClientSession(timeout=timeout) for _ in range(n)]
    try:
        yield sessions
    finally:
        await asyncio.gather(*(s.close() for s in sessions))


@asynccontextmanager
async def _open_session_pools(domains: set[str], n: int, timeout: aiohttp.ClientTimeout):
    """Async context manager yielding {domain: [session, ...]} pools."""
    pools: dict[str, list[aiohttp.ClientSession]] = {
        domain: [aiohttp.ClientSession(timeout=timeout) for _ in range(n)]
        for domain in domains
    }
    try:
        yield pools
    finally:
        await asyncio.gather(*(
            s.close() for pool in pools.values() for s in pool
        ))


def _wrap_request_error(exc: BaseException, context: str) -> NoReturn:
    """Log and re-raise a request error with context after retries are exhausted."""
    if isinstance(exc, asyncio.TimeoutError):
        logger.error("Timeout (all %d retries) %s", _MAX_RETRY_ATTEMPTS, context)
        raise asyncio.TimeoutError(f"Timeout {context}") from exc
    if isinstance(exc, aiohttp.ClientResponseError):
        logger.error("HTTP %d %s", exc.status, context)
        raise ValueError(f"HTTP {exc.status} {context}: {exc.message}") from exc
    logger.error("Network error (all %d retries) %s: %s", _MAX_RETRY_ATTEMPTS, context, exc)
    raise ValueError(f"Network error {context}: {exc}") from exc


def fetch_ogc_features(
    url: str,
    *,
    allowed_origins: list[str] | None = None,
    page_limit: int = 1000,
    timeout: float = 30.0,
    max_concurrent_requests: int = 10,
    n_sessions: int = 1,
) -> dict:
    """Fetch a complete GeoJSON FeatureCollection from an OGC API Features
    items endpoint.

    Uses ``numberMatched`` from the first page when available and otherwise
    performs speculative parallel paging via offset/limit. Both
    application/geo+json and application/flatgeobuf responses are decoded to
    in-memory GeoJSON FeatureCollection.

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
    n_sessions : int
        Number of independent client sessions. Each session has its own
        TCP connection pool; k8s routes new connections to different backend
        replicas. Set to the number of server replicas for even distribution.

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
        "fetch_ogc_features: url=%s page_limit=%d max_concurrent=%d n_sessions=%d",
        url, page_limit, max_concurrent_requests, n_sessions,
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def _run() -> dict:
        async with _open_sessions(n_sessions, client_timeout) as sessions:
            return await _fetch_ogc_features_async(
                url,
                sessions=sessions,
                allowed_origins=allowed_origins,
                page_limit=page_limit,
                semaphore=semaphore,
            )

    return asyncio.run(_run())


async def _fetch_ogc_features_async(
    url: str,
    *,
    sessions: list[aiohttp.ClientSession],
    allowed_origins: list[str] | None = None,
    page_limit: int = 1000,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Async implementation of fetch_ogc_features.

    Paging strategy (in priority order):

     1. ``numberMatched`` from the first page response — filter-aware count.
     2. Speculative parallel batching — no count available, so page in batches
         until a page returns fewer features than the limit.

    Pages cycle through *sessions* round-robin; k8s routes each new connection
    to a different backend replica.
    """
    if allowed_origins is not None:
        _assert_allowed(url, allowed_origins)

    session_cycle = cycle(sessions)

    # Always fetch the first page first so we can read numberMatched.
    first_page = await _fetch_page_with_offset(
        url, 0, page_limit, next(session_cycle), allowed_origins, semaphore
    )
    features: list[dict] = list(first_page.get("features", []))

    total_count, count_source = _resolve_total_count(first_page)

    if total_count is not None:
        remaining_offsets = list(range(page_limit, total_count, page_limit))
        if remaining_offsets:
            logger.info(
                "Parallel paging: %d features across %d pages "
                "(limit=%d, source=%s, sessions=%d)",
                total_count, 1 + len(remaining_offsets), page_limit, count_source,
                len(sessions),
            )
            tasks = [
                _fetch_page_with_offset(
                    url, offset, page_limit, next(session_cycle), allowed_origins, semaphore
                )
                for offset in remaining_offsets
            ]
            page_results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in page_results:
                if isinstance(result, BaseException):
                    raise result
                features.extend(result.get("features", []))
        else:
            logger.info(
                "%d features fit in one page (limit=%d, source=%s)",
                total_count, page_limit, count_source,
            )
    else:
        # No filter-aware count — speculative parallel batching: one request per
        # session at sequential offsets, stop when any page returns < page_limit
        # features.
        # Avoids relying on collection-wide counts for filtered requests; costs
        # at most len(sessions)-1 empty requests.
        if len(features) < page_limit:
            logger.debug("First page exhausts all results (%d features)", len(features))
        else:
            logger.info(
                "No count available; speculative parallel batching "
                "(%d sessions, %d features/page)",
                len(sessions), page_limit,
            )
            extra = await _fetch_pages_speculative(
                url, page_limit, sessions, allowed_origins, semaphore
            )
            features.extend(extra)

    logger.info(
        "fetch_ogc_features: fetched %d features total from %s", len(features), url
    )
    return {"type": "FeatureCollection", "features": features}


def _resolve_total_count(first_page: dict) -> tuple[int | None, str]:
    """Return (count, source) from numberMatched, or (None, '')."""
    if (count := first_page.get("numberMatched")) is not None:
        return count, "numberMatched"
    return None, ""


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
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        _wrap_request_error(exc, f"fetching page offset={offset} from {page_url!r}")


async def _fetch_pages_speculative(
    url: str,
    page_limit: int,
    sessions: list[aiohttp.ClientSession],
    allowed_origins: list[str] | None,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Parallel-batch paging without prior knowledge of total item count.

    Issues one request per session per batch at sequential offsets. Stops
    when any page returns fewer features than *page_limit* (signals end).
    Requires offset/limit paging (OGC API Features standard). Compatible
    with servers that disable computeNumberMatched.
    """
    features: list[dict] = []
    batch_size = len(sessions)
    offset = page_limit  # page 0 already fetched by caller
    session_cycle = cycle(sessions)

    while True:
        tasks = [
            _fetch_page_with_offset(
                url, offset + i * page_limit, page_limit,
                next(session_cycle), allowed_origins, semaphore,
            )
            for i in range(batch_size)
        ]
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        for page in pages:
            if isinstance(page, BaseException):
                raise page

        done = False
        for page in cast(list[dict], pages):
            chunk = page.get("features", [])
            features.extend(chunk)
            if len(chunk) < page_limit:
                done = True
                break
        if done:
            break
        offset += batch_size * page_limit

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
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        _wrap_request_error(exc, f"fetching FlatGeobuf from {url!r}")

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
    n_sessions: int = 1,
) -> dict[str, dict]:
    """Fetch all ``{href, type}`` reference objects concurrently.

    Creates *n_sessions* :class:`aiohttp.ClientSession` objects per distinct
    server domain.  k8s routes each new TCP connection to a different backend
    replica, so setting *n_sessions* to the replica count spreads page-level
    load across all pods.

    Parameters
    ----------
    references:
        Mapping of input key → ``{href, type}`` reference object
        (e.g. ``{"buildings": {"href": "https://…", "type": "application/geo+json"}}``).\
    allowed_origins:
        SSRF allowlist; same semantics as :func:`fetch_ogc_features`.
    timeout:
        Request timeout in seconds applied to every session. Default 30.
    page_limit:
        Items per page for OGC Features paging. Default 10000.
    max_concurrent_requests:
        Global semaphore shared across all sessions. Default 10.
    n_sessions:
        Sessions per domain for k8s replica distribution. Default 1.

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
            n_sessions=n_sessions,
        )
    )


def fetch_references_cached(
    ref_map: dict,
    *,
    allowed_origins: list[str],
    page_limit: int,
    max_concurrent_requests: int,
    n_sessions: int = 1,
) -> "dict[str, dict]":
    """Fetch remote references, optionally caching to disk via joblib.

    Set ``PFR_DEV_CACHE_DIR`` in your ``.env`` to a writable directory
    (e.g. ``data/intermediate``) to enable.  The cache is keyed by the
    function arguments, so changing any URL busts it automatically.
    Never set this variable in production — it bypasses live data.
    """
    cache_dir = settings.dev_cache_dir
    if cache_dir:
        logger.warning("DEV CACHE active — PFR_DEV_CACHE_DIR=%s", cache_dir)
    mem = joblib.Memory(cache_dir, verbose=0)
    _cached = mem.cache(_fetch_all_references_shim)
    _result = _cached(ref_map, allowed_origins, page_limit, max_concurrent_requests, n_sessions)  # type: ignore[arg-type]
    return cast("dict[str, dict]", _result)


def _fetch_all_references_shim(
    ref_map: dict,
    allowed_origins: list[str] | None,
    page_limit: int,
    max_concurrent_requests: int,
    n_sessions: int = 1,
) -> "dict[str, dict]":
    """Positional-args shim around fetch_all_references for joblib caching."""
    return fetch_all_references(
        ref_map,
        allowed_origins=allowed_origins,
        page_limit=page_limit,
        max_concurrent_requests=max_concurrent_requests,
        n_sessions=n_sessions,
    )


def _domain(href: str) -> str:
    """Return scheme://netloc for grouping URLs by server."""
    parsed = urlparse(href)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _fetch_reference_async(
    ref: dict,
    *,
    sessions: list[aiohttp.ClientSession],
    allowed_origins: list[str] | None,
    page_limit: int,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Dispatch a single ``{href, type}`` reference to the appropriate fetcher."""
    href: str = ref["href"]
    media_type: str = ref["type"]
    if media_type == "application/flatgeobuf":
        return await _fetch_flatgeobuf_async(
            href, session=sessions[0], allowed_origins=allowed_origins
        )
    return await _fetch_ogc_features_async(
        href,
        sessions=sessions,
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
    n_sessions: int = 1,
) -> dict[str, dict]:
    """Fetch all references concurrently, n_sessions sessions per domain."""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    semaphore = asyncio.Semaphore(max_concurrent_requests)
    domains = {_domain(ref["href"]) for ref in references.values()}

    async with _open_session_pools(domains, n_sessions, client_timeout) as session_pools:
        coros = {
            key: _fetch_reference_async(
                ref,
                sessions=session_pools[_domain(ref["href"])],
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
