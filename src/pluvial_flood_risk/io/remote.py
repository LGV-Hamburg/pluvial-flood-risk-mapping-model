"""OGC API Features download with parallel paging, SSRF protection, and FlatGeobuf support."""

import asyncio
import os
import tempfile
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp
import geopandas as gpd
from fastprocesses.core.exceptions import SSRFBlockedError


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
    return asyncio.run(
        _fetch_ogc_features_async(
            url,
            allowed_origins=allowed_origins,
            page_limit=page_limit,
            timeout=timeout,
            max_concurrent_requests=max_concurrent_requests,
        )
    )


async def _fetch_ogc_features_async(
    url: str,
    *,
    allowed_origins: list[str] | None = None,
    page_limit: int = 1000,
    timeout: float = 30.0,
    max_concurrent_requests: int = 10,
) -> dict:
    """Async implementation of fetch_ogc_features.

    Attempts parallel paging via itemCount from collections endpoint.
    Falls back to sequential next-link following if unavailable.
    """
    if allowed_origins is not None:
        _assert_allowed(url, allowed_origins)

    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(max_concurrent_requests)

    # Try to get itemCount from the OGC collections endpoint
    item_count = await _get_item_count(url, timeout, allowed_origins)

    async with aiohttp.ClientSession() as session:
        if item_count is not None:
            # Parallel paging: fetch all pages concurrently using offset/limit
            features = await _fetch_pages_parallel(
                url, item_count, page_limit, session, timeout, allowed_origins, semaphore
            )
        else:
            # Fallback: sequential next-link following
            features = await _fetch_pages_sequential(
                url, page_limit, session, timeout, allowed_origins, semaphore
            )

    return {"type": "FeatureCollection", "features": features}


async def _get_item_count(
    url: str,
    timeout: float,
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

    # Reconstruct URL without 'items' path segment
    collections_path = "/".join(path_parts[:-1])
    collections_url = urlunparse(parsed._replace(path=collections_path))

    try:
        if allowed_origins is not None:
            _assert_allowed(collections_url, allowed_origins)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                collections_url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                data = await response.json()

                # Collection object should have itemCount at root level
                if isinstance(data, dict) and "itemCount" in data:
                    return data.get("itemCount")

                return None
    except Exception:
        # Collections endpoint unavailable or unparseable; fall back to next-link
        return None


async def _fetch_pages_parallel(
    url: str,
    item_count: int,
    page_limit: int,
    session: aiohttp.ClientSession,
    timeout: float,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetch all pages in parallel using offset/limit parameters."""
    offsets = list(range(0, item_count, page_limit))

    tasks = [
        _fetch_page_with_offset(url, offset, page_limit, session, timeout, allowed_origins, semaphore)
        for offset in offsets
    ]

    page_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect errors and merge features
    features = []
    for result in page_results:
        if isinstance(result, Exception):
            raise result
        features.extend(result.get("features", []))

    return features


async def _fetch_page_with_offset(
    url: str,
    offset: int,
    limit: int,
    session: aiohttp.ClientSession,
    timeout: float,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Fetch a single page using offset and limit parameters."""
    page_url = _with_limit(_with_offset(url, offset), limit)

    if allowed_origins is not None:
        _assert_allowed(page_url, allowed_origins)

    async with (semaphore or asyncio.Semaphore(1)):
        async with session.get(
            page_url,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            response.raise_for_status()
            return await response.json()


async def _fetch_pages_sequential(
    url: str,
    page_limit: int,
    session: aiohttp.ClientSession,
    timeout: float,
    allowed_origins: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetch pages sequentially following links[rel=next]."""
    features = []
    next_url: str | None = _with_limit(url, page_limit)

    while next_url:
        if allowed_origins is not None:
            _assert_allowed(next_url, allowed_origins)

        async with (semaphore or asyncio.Semaphore(1)):
            async with session.get(
                next_url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                fc = await response.json()

        features.extend(fc.get("features", []))
        next_url = _next_link(fc)

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
