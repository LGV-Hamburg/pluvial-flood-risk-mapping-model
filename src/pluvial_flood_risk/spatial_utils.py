"""Spatial utility helpers shared across pipeline branches."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from .models.field_names import (
    LIVING_AREA_M2_STATISTICAL_UNIT,
    RESIDENTS_IN_STATISTICAL_UNIT,
    STATISTICAL_UNIT_ID,
)


def join_statistical_unit_attributes(
    buildings: gpd.GeoDataFrame,
    stat_units: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Join census attributes from *stat_units* onto *buildings*.

    The join strategy is chosen automatically:

    1. **Attribute join** — when *buildings* already carries a
       ``statistical_unit_id`` column.  Each building is matched to the
       statistical unit with the same identifier.  Fast and CRS-independent.

    2. **Spatial centroid join** — when ``statistical_unit_id`` is absent.
       The centroid of each building footprint is tested against the unit
       polygons (``within`` predicate) after reprojecting *stat_units* to
       the CRS of *buildings*.  Centroids guarantee that every building is
       assigned to exactly one unit, even when footprint edges touch a shared
       boundary.

    Parameters
    ----------
    buildings:
        Building GeoDataFrame, already projected to a metric CRS.  Must not
        yet contain ``residents_in_statistical_unit`` or
        ``living_area_m2_statistical_unit`` (those are the columns this
        function adds).
    stat_units:
        Statistical unit GeoDataFrame with columns ``residents``,
        ``living_area_m2``, and — for the attribute-join path —
        ``statistical_unit_id``.  May carry any CRS; will be reprojected to
        *buildings* CRS when needed.

    Returns
    -------
    gpd.GeoDataFrame
        *buildings* with two additional columns:
        ``residents_in_statistical_unit`` and
        ``living_area_m2_statistical_unit``.
    """
    stat_units = stat_units.rename(
        columns={
            "residents": RESIDENTS_IN_STATISTICAL_UNIT,
            "living_area_m2": LIVING_AREA_M2_STATISTICAL_UNIT,
        }
    )

    if STATISTICAL_UNIT_ID in buildings.columns:
        # Strategy 1: attribute join via statistical_unit_id
        join_cols = [
            STATISTICAL_UNIT_ID,
            RESIDENTS_IN_STATISTICAL_UNIT,
            LIVING_AREA_M2_STATISTICAL_UNIT,
        ]
        stat_df = pd.DataFrame(stat_units[join_cols]).drop_duplicates(
            subset=STATISTICAL_UNIT_ID
        )
        return buildings.merge(stat_df, on=STATISTICAL_UNIT_ID, how="left")

    # Strategy 2: spatial centroid join (within)
    stat_units = stat_units.to_crs(buildings.crs)
    join_cols = [
        RESIDENTS_IN_STATISTICAL_UNIT,
        LIVING_AREA_M2_STATISTICAL_UNIT,
        "geometry",
    ]
    centroids = buildings.copy()
    centroids.geometry = buildings.geometry.centroid
    joined = centroids.sjoin(stat_units[join_cols], how="left", predicate="within")
    # Guard against overlapping unit polygons producing duplicate rows
    joined = joined[~joined.index.duplicated(keep="first")]

    result = buildings.copy()
    result[RESIDENTS_IN_STATISTICAL_UNIT] = joined[RESIDENTS_IN_STATISTICAL_UNIT]
    result[LIVING_AREA_M2_STATISTICAL_UNIT] = joined[LIVING_AREA_M2_STATISTICAL_UNIT]
    return result
