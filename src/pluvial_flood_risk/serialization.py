"""Output serialization helpers for the pluvial flood risk pipeline."""

from __future__ import annotations

import pandas as pd


def json_scalar(value) -> float | int | str | bool | None:
    """Convert a pandas / numpy scalar to a JSON-safe Python value."""
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def serialize_output_value(value, decimal_precision: int | None):
    """Convert an output value to a JSON-safe scalar with optional rounding."""
    scalar = json_scalar(value)
    if scalar is None or decimal_precision is None:
        return scalar
    if isinstance(scalar, float):
        rounded = round(scalar, decimal_precision)
        return 0.0 if rounded == 0 else rounded
    return scalar
