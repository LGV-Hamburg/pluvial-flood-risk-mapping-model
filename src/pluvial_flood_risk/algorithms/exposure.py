"""Exposure pipeline — Step 2.

Computes building-level resident counts following Vogelbacher et al. (2025).

Pipeline sequence
-----------------
Step 2a  compute_building_living_area()           → building_living_area_m2  (Eq. 8a)
Step 2b  compute_residents_per_building()         → residents_per_building   (Eq. 8)
Step 2c  compute_residents_ground_floor()         → residents_ground_floor   (Eq. 9)
Step 2d  compute_exposure_mobility_accessibility() → exposure_mobility_accessibility (Eq. 10)

Input assumptions
-----------------
The caller must supply a DataFrame that already contains the building footprint
area as ``building_footprint_area_m2``.  When the input arrives as a GeoJSON
FeatureCollection the process layer derives this column from the polygon
geometry (``gdf.geometry.area``) before invoking this module.

The process layer ensures two statistical-unit columns are available before
this module runs:

* ``residents_in_statistical_unit`` — provided directly or joined from the
    statistical-units dataset
* ``living_area_m2_statistical_unit`` — derived from building geometry,
    floors, and building type within each statistical unit

Formulas (original ArcPy field expressions)
--------------------------------------------
Area_house = (Floors - (Building_type - 1)) * Shape_Area
R          = Area_house / LivingArea * Residents
R_G        = -(Building_type - 2) * R / Floors
EMA        = R   (all residents per building; mobility risk affects every floor)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_LIVING_AREA_M2,
    BUILDING_TYPE,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    FLOORS_ABOVE_GROUND,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_IN_STATISTICAL_UNIT,
    RESIDENTS_PER_BUILDING,
)

# ---------------------------------------------------------------------------
# Step 2a — Net usable building area
# ---------------------------------------------------------------------------


def compute_building_living_area(df: pd.DataFrame) -> pd.Series:
    """Net usable floor area of the building (Step 2, Eq. 8a).

    .. math::

        \\text{Area\\_house} = (Floors - (Building\\_type - 1)) \\times Shape\\_Area

    The term ``Building_type - 1`` removes the non-residential floors:
    building type 1 has all floors residential (subtract 0),
    building type 2 has the ground floor non-residential (subtract 1).

    Parameters
    ----------
    df:
        DataFrame with ``floors_above_ground``, ``building_type``, and
        ``building_footprint_area_m2`` columns.

    Returns
    -------
    pd.Series
        ``building_living_area_m2`` — net usable area [m²].
    """
    net_floors = df[FLOORS_ABOVE_GROUND] - (df[BUILDING_TYPE] - 1)
    return (net_floors * df[BUILDING_FOOTPRINT_AREA_M2]).rename(BUILDING_LIVING_AREA_M2)


# ---------------------------------------------------------------------------
# Step 2b — Residents per building
# ---------------------------------------------------------------------------


def compute_residents_per_building(df: pd.DataFrame) -> pd.Series:
    """Area-weighted resident count per building (Step 2, Eq. 8).

    .. math::

        R = \\frac{\\text{Area\\_house}}{LivingArea} \\times Residents

    The statistical-unit total residents are distributed across buildings
    proportionally to each building's share of the total residential floor area.

    Parameters
    ----------
    df:
        DataFrame with ``building_living_area_m2``,
        ``living_area_m2_statistical_unit``, and
        ``residents_in_statistical_unit`` columns.

    Returns
    -------
    pd.Series
        ``residents_per_building`` (R).  ``NaN`` where
        ``living_area_m2_statistical_unit`` is zero or missing (division guard).
    """
    living_area = df[LIVING_AREA_M2_STATISTICAL_UNIT]
    result = np.divide(
        df[BUILDING_LIVING_AREA_M2] * df[RESIDENTS_IN_STATISTICAL_UNIT],
        living_area,
        out=np.full(len(df), np.nan),
        where=living_area.to_numpy() > 0.0,
    )
    return pd.Series(result, index=df.index, name=RESIDENTS_PER_BUILDING)


# ---------------------------------------------------------------------------
# Step 2c — Ground-floor residents (EWB)
# ---------------------------------------------------------------------------


def compute_residents_ground_floor(df: pd.DataFrame) -> pd.Series:
    """Ground-floor resident count / well-being exposure index (Step 2, Eq. 9).

    .. math::

        R_G = \\frac{-(Building\\_type - 2) \\times R}{Floors}

    For building type 1 (all floors residential):
        ``-(1 - 2) = 1``  →  R_G = R / Floors  (even distribution across floors)

    For building type 2 (ground floor commercial):
        ``-(2 - 2) = 0``  →  R_G = 0  (no residents on ground floor)

    Parameters
    ----------
    df:
        DataFrame with ``building_type``, ``residents_per_building``, and
        ``floors_above_ground`` columns.

    Returns
    -------
    pd.Series
        ``residents_ground_floor`` (R_G / EWB).  ``NaN`` where
        ``floors_above_ground`` is zero or missing.
    """
    floors = df[FLOORS_ABOVE_GROUND]
    numerator = -(df[BUILDING_TYPE] - 2) * df[RESIDENTS_PER_BUILDING]
    result = np.divide(
        numerator.to_numpy(dtype=float),
        floors.to_numpy(dtype=float),
        out=np.full(len(df), np.nan),
        where=floors.to_numpy() > 0.0,
    )
    return pd.Series(result, index=df.index, name=RESIDENTS_GROUND_FLOOR)


# ---------------------------------------------------------------------------
# Step 2d — Mobility & Accessibility exposure index (EMA)
# ---------------------------------------------------------------------------


def compute_exposure_mobility_accessibility(df: pd.DataFrame) -> pd.Series:
    """Mobility & accessibility exposure index (Step 2, Eq. 10).

    EMA = R (total residents per building).

    For mobility risk, flooding affects every resident's ability to access or
    leave the building regardless of which floor they live on — unlike the
    well-being hazard which primarily affects ground-floor residents.
    EMA is therefore identical to R but named separately to make its role in
    the PFR_MA formula explicit.

    Parameters
    ----------
    df:
        DataFrame with a ``residents_per_building`` column.

    Returns
    -------
    pd.Series
        ``exposure_mobility_accessibility`` (EMA).
    """
    return df[RESIDENTS_PER_BUILDING].rename(EXPOSURE_MOBILITY_ACCESSIBILITY)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def run_exposure_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Run the complete exposure pipeline (Step 2).

    Parameters
    ----------
    df:
        DataFrame with one row per building.  Must contain:

        * ``building_footprint_area_m2``  — footprint area [m²] (from geometry)
        * ``floors_above_ground``
        * ``building_type``
        * ``residents_in_statistical_unit``
        * ``living_area_m2_statistical_unit``

    Returns
    -------
    pd.DataFrame
        A copy of *df* with four additional columns:

        * ``building_living_area_m2``
        * ``residents_per_building``
        * ``residents_ground_floor``
        * ``exposure_mobility_accessibility``
    """
    out = df.copy()

    # Step 2a — Net usable floor area
    out[BUILDING_LIVING_AREA_M2] = compute_building_living_area(out)

    # Step 2b — Area-weighted residents per building  (Eq. 8)
    out[RESIDENTS_PER_BUILDING] = compute_residents_per_building(out)

    # Step 2c — Ground-floor residents / EWB  (Eq. 9)
    out[RESIDENTS_GROUND_FLOOR] = compute_residents_ground_floor(out)

    # Step 2d — Mobility exposure index / EMA  (Eq. 10)
    out[EXPOSURE_MOBILITY_ACCESSIBILITY] = compute_exposure_mobility_accessibility(out)

    return out
