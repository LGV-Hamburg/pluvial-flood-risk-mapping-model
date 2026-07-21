"""Output serialization helpers for the pluvial flood risk pipeline."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from .algorithms.classification import ClassificationResult
from .models.inputs import PipelineParams
from .spatial_utils import convert_geojson_to_gdf


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


def build_enriched_features(
    source_features: list[dict],
    classified: ClassificationResult,
    input: PipelineParams,
    output_columns: list[str],
    candidate_cols: list[str],
    input_building_output_columns: list[str],
) -> tuple[list[dict], gpd.GeoDataFrame]:
    """Return ``(enriched_features, buildings_gdf)`` for downstream outputs.

    Attaches numeric columns from ``classified.data`` to the GeoDataFrame
    and serialises the building features according to ``input.output_settings``.

    Args:
        source_features: Raw GeoJSON feature dicts from the buildings input.
        classified: Result of the classification pipeline, including ``data``
            DataFrame and break thresholds.
        input: Parsed pipeline parameters (CRS, output settings, etc.).
        output_columns: Computed columns to copy onto each output feature.
        candidate_cols: Columns to attach to the GeoDataFrame for aggregation.
        input_building_output_columns: Source building properties to carry
            through when ``include_input_building_properties`` is enabled.
    """
    buildings_gdf = convert_geojson_to_gdf(source_features, input.target_crs)

    if "id" in buildings_gdf.columns:
        buildings_gdf = buildings_gdf.set_index("id")
    buildings_gdf.index = buildings_gdf.index.astype(str)

    classified_df = classified.data.copy()
    classified_df.index = classified_df.index.astype(str)

    for col in candidate_cols:
        if col in classified_df.columns:
            buildings_gdf[col] = classified_df[col].reindex(
                buildings_gdf.index
            ).values

    enriched_features = []
    for feature in source_features:
        feature_id = feature.get("id") or feature.get("properties", {}).get("id")
        if feature_id is None:
            raise ValueError(
                "Each building feature must provide an "
                "id either at the feature top level or in properties.id."
            )
        building_id = str(feature_id)
        if building_id not in classified.data.index:
            raise ValueError(
                f"Missing merged results for building id {building_id!r}."
            )

        source_properties = feature.get("properties", {})

        if input.output_settings.include_input_building_properties:
            properties = {
                column: source_properties[column]
                for column in input_building_output_columns
                if column in source_properties
            }
        else:
            properties = {}
        row = classified.data.loc[building_id]
        for column in output_columns:
            # When a column is absent from classified.data (e.g. citywide process
            # where execute_single pre-computes most columns and merge_results only
            # adds classification labels), fall back to the feature's own properties.
            value = (
                row[column]
                if column in classified.data.columns
                else source_properties.get(column)
            )
            properties[column] = serialize_output_value(
                value, input.output_settings.decimal_precision
            )
        enriched_features.append({**feature, "properties": properties})

    return enriched_features, buildings_gdf
