"""Minimal smoke tests for pluvial_flood_risk."""

from __future__ import annotations

import pluvial_flood_risk


def test_package_importable() -> None:
    """The package must be importable without errors."""
    assert pluvial_flood_risk is not None
