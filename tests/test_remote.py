from __future__ import annotations

import asyncio
from typing import cast

import aiohttp

from pluvial_flood_risk.io import remote


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