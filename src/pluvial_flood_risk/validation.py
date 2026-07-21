"""Input validation helpers for the pluvial flood risk pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError
from referencing import Resource

logger = logging.getLogger(__name__)


def load_schema_file(path: str) -> Resource:
    """Load a JSON Schema file and return a referencing ``Resource``."""
    schema = json.loads(Path(path).read_text())
    return Resource.from_contents(schema)


def drop_invalid_features(fc: dict, props_model: type, layer_name: str) -> dict:
    """Return a new FeatureCollection with invalid features removed.

    A feature is considered invalid when any of the following apply:

    * The ``"geometry"`` key is absent entirely.
    * The geometry type is ``"GeometryCollection"`` (membership of only
      Polygon/MultiPolygon cannot be guaranteed).
    * Feature properties fail validation against *props_model*.

    Only called when ``PFR_DROP_INVALID_INPUT_FEATURES=true``.
    """
    valid_features = []
    dropped = 0
    for feature in fc.get("features", []):
        if "geometry" not in feature:
            logger.warning(
                "late_validate: dropping '%s' feature — missing 'geometry' key",
                layer_name,
            )
            dropped += 1
            continue
        geom_type = (feature["geometry"] or {}).get("type")
        if geom_type == "GeometryCollection":
            feature_id = (feature.get("properties") or {}).get("id", "<unknown>")
            logger.warning(
                "late_validate: dropping '%s' feature %r — "
                "GeometryCollection cannot be guaranteed to contain only "
                "Polygon/MultiPolygon members",
                layer_name, feature_id,
            )
            dropped += 1
            continue
        try:
            props_model.model_validate(feature.get("properties") or {})
            valid_features.append(feature)
        except ValidationError as exc:
            feature_id = (feature.get("properties") or {}).get("id", "<unknown>")
            logger.warning(
                "late_validate: dropping '%s' feature %r — validation error: %s",
                layer_name, feature_id, exc,
            )
            dropped += 1

    if dropped:
        logger.warning(
            "late_validate: dropped %d/%d invalid '%s' features "
            "(PFR_DROP_INVALID_INPUT_FEATURES=true)",
            dropped, dropped + len(valid_features), layer_name,
        )
    logger.debug(
        "late_validate: '%s' OK after dropping (%d features kept)",
        layer_name, len(valid_features),
    )
    return {**fc, "features": valid_features}
