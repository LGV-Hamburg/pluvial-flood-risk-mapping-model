"""Spatial utility helpers shared across pipeline branches."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from .models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_TYPE,
    CHILDREN_UNDER_10,
    ELDERLY_SINGLES_OVER_65,
    FLOORS_ABOVE_GROUND,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    NO_HIGHSCHOOL_DIPLOMA,
    RESIDENTS_IN_STATISTICAL_UNIT,
    SINGLE_PARENTS,
    SOCIAL_WELFARE_RECIPIENTS,
    STATISTICAL_UNIT_ID,
)

# Census columns on statistical units that should be joined onto buildings
# when absent.  Order does not matter.
_CENSUS_COLUMNS = [
    RESIDENTS_IN_STATISTICAL_UNIT,
    CHILDREN_UNDER_10,
    ELDERLY_SINGLES_OVER_65,
    SINGLE_PARENTS,
    SOCIAL_WELFARE_RECIPIENTS,
    NO_HIGHSCHOOL_DIPLOMA,
]


def join_statistical_unit_attributes(
    buildings: gpd.GeoDataFrame,
    stat_units: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Join census attributes from *stat_units* onto *buildings*.

    Columns joined (when present on *stat_units* and absent from *buildings*):
    ``residents_in_statistical_unit``, ``children_under_10``,
    ``elderly_singles_over_65``, ``social_welfare_recipients``,
    ``no_highschool_diploma``.

    Additionally, ``living_area_m2_statistical_unit`` is always derived from
    building geometry grouped by statistical unit.

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
        Building GeoDataFrame, already projected to a metric CRS.
    stat_units:
        Statistical unit GeoDataFrame carrying census attributes. May carry
        any CRS; will be reprojected to *buildings* CRS when needed.

    Returns
    -------
    gpd.GeoDataFrame
        *buildings* with census columns and ``living_area_m2_statistical_unit``
        added.
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

    # Determine which census columns are available on stat_units and missing
    # from buildings — those will be joined.
    cols_to_join = [
        col for col in _CENSUS_COLUMNS
        if col in stat_units.columns and col not in buildings.columns
    ]

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
        join_cols = [STATISTICAL_UNIT_ID] + cols_to_join
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
    sjoin_cols = cols_to_join + ["geometry"]
    if STATISTICAL_UNIT_ID in stat_units.columns:
        sjoin_cols.insert(0, STATISTICAL_UNIT_ID)

    centroids = buildings.copy()
    centroids.geometry = buildings.geometry.centroid
    joined = centroids.sjoin(stat_units[sjoin_cols], how="left", predicate="within")
    # Guard against overlapping unit polygons producing duplicate rows
    joined = joined[~joined.index.duplicated(keep="first")]

    result = buildings.copy()
    if STATISTICAL_UNIT_ID in joined.columns:
        result[STATISTICAL_UNIT_ID] = joined[STATISTICAL_UNIT_ID]
        unit_key = result[STATISTICAL_UNIT_ID]
    else:
        unit_key = joined["index_right"]

    for col in cols_to_join:
        result[col] = joined[col].values

    result[LIVING_AREA_M2_STATISTICAL_UNIT] = (
        pd.Series(building_living_area, index=result.index)
        .groupby(unit_key)
        .transform("sum")
    )
    return result
