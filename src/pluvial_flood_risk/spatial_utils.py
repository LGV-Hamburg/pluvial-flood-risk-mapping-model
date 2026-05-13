"""Spatial utility helpers shared across pipeline branches."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from .models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_TYPE,
    FLOORS_ABOVE_GROUND,
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
        Statistical unit GeoDataFrame with column ``residents`` and — for the
        attribute-join path — ``statistical_unit_id``. May carry any CRS; will
        be reprojected to *buildings* CRS when needed.

    Returns
    -------
    gpd.GeoDataFrame
        *buildings* with two additional columns:
        ``residents_in_statistical_unit`` and
        ``living_area_m2_statistical_unit`` (derived from building features).
    """
    stat_units = stat_units.rename(columns={"residents": RESIDENTS_IN_STATISTICAL_UNIT})

    required_building_cols = {
        FLOORS_ABOVE_GROUND,
        BUILDING_TYPE,
        BUILDING_FOOTPRINT_AREA_M2,
    }
    missing_cols = sorted(required_building_cols - set(buildings.columns))
    if missing_cols:
        raise ValueError(
            "Cannot derive living_area_m2_statistical_unit; missing building "
            f"columns: {missing_cols}"
        )

    building_living_area = (
        (buildings[FLOORS_ABOVE_GROUND] - (buildings[BUILDING_TYPE] - 1))
        * buildings[BUILDING_FOOTPRINT_AREA_M2]
    )

    if STATISTICAL_UNIT_ID in buildings.columns:
        # Strategy 1: attribute join via statistical_unit_id
        if STATISTICAL_UNIT_ID not in stat_units.columns:
            raise ValueError(
                "statistical_units must contain 'statistical_unit_id' for "
                "attribute-based joining."
            )
        join_cols = [STATISTICAL_UNIT_ID, RESIDENTS_IN_STATISTICAL_UNIT]
        stat_df = pd.DataFrame(stat_units[join_cols]).drop_duplicates(
            subset=STATISTICAL_UNIT_ID
        )
        result = buildings.merge(stat_df, on=STATISTICAL_UNIT_ID, how="left")
        result_building_living_area = (
            (result[FLOORS_ABOVE_GROUND] - (result[BUILDING_TYPE] - 1))
            * result[BUILDING_FOOTPRINT_AREA_M2]
        )
        result[LIVING_AREA_M2_STATISTICAL_UNIT] = result_building_living_area.groupby(
            result[STATISTICAL_UNIT_ID]
        ).transform("sum")
        return result

    # Strategy 2: spatial centroid join (within)
    if buildings.crs is None:
        raise ValueError("buildings GeoDataFrame must have a CRS set")
    stat_units = stat_units.to_crs(buildings.crs)
    join_cols = [RESIDENTS_IN_STATISTICAL_UNIT, "geometry"]
    if STATISTICAL_UNIT_ID in stat_units.columns:
        join_cols.insert(0, STATISTICAL_UNIT_ID)

    centroids = buildings.copy()
    centroids.geometry = buildings.geometry.centroid
    joined = centroids.sjoin(stat_units[join_cols], how="left", predicate="within")
    # Guard against overlapping unit polygons producing duplicate rows
    joined = joined[~joined.index.duplicated(keep="first")]

    result = buildings.copy()
    if STATISTICAL_UNIT_ID in joined.columns:
        result[STATISTICAL_UNIT_ID] = joined[STATISTICAL_UNIT_ID]
        unit_key = result[STATISTICAL_UNIT_ID]
    else:
        unit_key = joined["index_right"]
    result[RESIDENTS_IN_STATISTICAL_UNIT] = joined[RESIDENTS_IN_STATISTICAL_UNIT]
    result[LIVING_AREA_M2_STATISTICAL_UNIT] = (
        pd.Series(building_living_area, index=result.index)
        .groupby(unit_key)
        .transform("sum")
    )
    return result
