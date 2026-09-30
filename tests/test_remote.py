from __future__ import annotations

import asyncio
from typing import cast

import aiohttp
import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import Point

from pluvial_flood_risk.io import remote


def test_get_json_reprojects_content_crs_and_preserves_feature_identity():
    east, north = Transformer.from_crs(
        "EPSG:4326", "EPSG:25832", always_xy=True
    ).transform(10.0, 53.0)
    feature = {
        "type": "Feature",
        "id": "building-1",
        "geometry": {"type": "Point", "coordinates": [east, north]},
        "properties": {"id": 7, "building_type": 1},
    }

    class Response:
        headers = {"Content-Crs": "<http://www.opengis.net/def/crs/EPSG/0/25832>"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return {"type": "FeatureCollection", "features": [feature]}

    class Session:
        def get(self, url):
            return Response()

    result = asyncio.run(
        remote._get_json("https://example.com/items", Session(), asyncio.Semaphore(1))
    )

    projected_feature = result["features"][0]
    assert projected_feature["geometry"]["coordinates"] == pytest.approx([10.0, 53.0])
    assert projected_feature["id"] == "building-1"
    assert projected_feature["properties"] == feature["properties"]


def test_get_json_rejects_projected_coordinates_without_content_crs():
    class Response:
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return {
                "features": [{
                    "geometry": {"type": "Point", "coordinates": [567109, 5872738]}
                }]
            }

    class Session:
        def get(self, url):
            return Response()

    with pytest.raises(ValueError, match="Content-Crs"):
        asyncio.run(
            remote._get_json("https://example.com/items", Session(), asyncio.Semaphore(1))
        )


def test_decode_flatgeobuf_uses_embedded_crs(monkeypatch):
    east, north = Transformer.from_crs(
        "EPSG:4326", "EPSG:25832", always_xy=True
    ).transform(10.0, 53.0)
    projected = gpd.GeoDataFrame(
        {"id": [7]}, geometry=[Point(east, north)], crs="EPSG:25832"
    )
    monkeypatch.setattr(remote.gpd, "read_file", lambda path: projected)

    decoded = remote.decode_flatgeobuf_bytes(b"example")

    assert decoded["features"][0]["geometry"]["coordinates"] == pytest.approx(
        (10.0, 53.0)
    )


def test_flatgeobuf_url_uses_content_crs_when_embedded_crs_is_missing(monkeypatch):
    east, north = Transformer.from_crs(
        "EPSG:4326", "EPSG:25832", always_xy=True
    ).transform(10.0, 53.0)
    projected = gpd.GeoDataFrame({"id": [7]}, geometry=[Point(east, north)])
    monkeypatch.setattr(remote.gpd, "read_file", lambda path: projected)

    class Response:
        headers = {"Content-Crs": "<http://www.opengis.net/def/crs/EPSG/0/25832>"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def read(self):
            return b"example"

    class Session:
        def get(self, url):
            return Response()

    decoded = asyncio.run(
        remote._fetch_flatgeobuf_async(
            "https://example.com/buildings.fgb", session=Session()
        )
    )

    assert decoded["features"][0]["geometry"]["coordinates"] == pytest.approx(
        (10.0, 53.0)
    )


def test_paged_geojson_normalizes_each_response_crs():
    east, north = Transformer.from_crs(
        "EPSG:4326", "EPSG:25832", always_xy=True
    ).transform(10.0, 53.0)

    class Response:
        def __init__(self, url):
            self.projected = "offset=0" in url
            self.headers = {
                "Content-Crs": "<http://www.opengis.net/def/crs/EPSG/0/25832>"
                if self.projected else "<http://www.opengis.net/def/crs/OGC/1.3/CRS84>"
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            coordinates = [east, north] if self.projected else [10.01, 53.01]
            return {
                "type": "FeatureCollection",
                "numberMatched": 2,
                "features": [{
                    "type": "Feature", "geometry": {
                        "type": "Point", "coordinates": coordinates
                    }, "properties": {},
                }],
            }

    class Session:
        def get(self, url):
            return Response(url)

    result = asyncio.run(
        remote._fetch_ogc_features_async(
            "https://example.com/items",
            sessions=cast(list[aiohttp.ClientSession], [Session()]),
            page_limit=1,
            semaphore=asyncio.Semaphore(2),
        )
    )

    assert result["features"][0]["geometry"]["coordinates"] == pytest.approx(
        (10.0, 53.0)
    )
    assert result["features"][1]["geometry"]["coordinates"] == pytest.approx(
        (10.01, 53.01)
    )


def test_fetch_ogc_features_uses_speculative_batching_without_number_matched(
    monkeypatch,
) -> None:
    calls: list[int] = []

    async def fake_fetch_page_with_offset(
        url: str,
        offset: int,
        limit: int,
        session: object,
        allowed_origins: list[str] | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> dict:
        calls.append(offset)
        if offset == 0:
            return {"features": [{"id": "a"}, {"id": "b"}]}
        if offset == 2:
            return {"features": [{"id": "c"}, {"id": "d"}]}
        return {"features": [{"id": "e"}]}

    monkeypatch.setattr(remote, "_fetch_page_with_offset", fake_fetch_page_with_offset)

    result = asyncio.run(
        remote._fetch_ogc_features_async(
            "https://example.com/collections/buildings/items",
            sessions=cast(list[aiohttp.ClientSession], [object(), object()]),
            allowed_origins=None,
            page_limit=2,
            semaphore=asyncio.Semaphore(10),
        )
    )

    assert calls == [0, 2, 4]
    assert [feature["id"] for feature in result["features"]] == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]