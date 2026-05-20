"""Hazard pipeline — Module 4: Well-Being / Health (Steps 4.1–4.2).

Implements the 2 m building-buffer spatial intersection and the log-normal CDF
summation following Vogelbacher et al. (2025).  Reference ArcPy scripts:
    4_Step1_Calculating_Hazard_WB.py
    4_Step2_Calculating_Hazard_WB.py

Pipeline sequence
-----------------
Step 4.1  compute_wb_flood_fractions()  → 2 × N_depths intermediate columns
Step 4.2  compute_hazard_wb()           → hazard_well_being

Step 4.1 — Spatial preprocessing
----------------------------------
For each water-depth class present in the combined flood layer:

    Bu2 = Buffer(building, 2 m, OUTSIDE_ONLY)
    Tabulate intersection of Bu2 × flood_features_for_that_depth
        → flooded_area_m2_2m_{depth}cm, flooded_fraction_2m_{depth}cm

The original ArcPy script iterated over one shapefile per depth class; in our
implementation all depth classes are merged into a single GeoDataFrame
(``flood_layers``) with a required ``level_cm`` property per feature.

Step 4.2 — Hazard index (Eq. 11/12)
-------------------------------------
    HWB = Σ  lognorm.cdf(fraction_at_depth × 4, σ)
          ───── over all depth classes that have a non-zero intersection ─────

where σ is ``HazardParams.lognormal_shape`` (default 0.25) and μ is fixed at 0.

The ``× 4`` factor and the μ = 0 / σ = 0.25 convention are taken verbatim from
the ArcPy scripts and paper.  Unlike HMA (which takes the *maximum* across
buffer zones) HWB *sums* across depth classes — each class represents an
independent water-depth scenario.

Column naming convention for intermediate outputs
-------------------------------------------------
Step 4.1 column names are depth-class-specific and therefore cannot be fixed
constants.  Use the helper functions ``flooded_fraction_col(depth_cm)`` and
``flooded_area_col(depth_cm)`` to generate them, e.g.::

    flooded_fraction_col(30)  →  "flooded_fraction_2m_30cm"
    flooded_area_col(30)      →  "flooded_area_m2_2m_30cm"

``compute_hazard_wb`` auto-detects these columns by the prefix
``"flooded_fraction_2m_"`` so callers do not need to track depth classes
explicitly.

Open-source equivalents of the arcpy operations
------------------------------------------------
| arcpy                  | geopandas / shapely                             |
|------------------------|-------------------------------------------------|
| Buffer(OUTSIDE_ONLY)   | gdf.buffer(r).difference(gdf.union_all())       |
| Erase(A, B)            | gpd.overlay(A, B, how='difference')             |
| TabulateIntersection   | overlay → .area, then groupby('building_id').sum|
"""

from __future__ import annotations

import logging
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.stats import lognorm
from shapely.ops import unary_union

from ..models.field_names import HAZARD_WELL_BEING
from ..models.inputs import HazardParams

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column-name helpers
# ---------------------------------------------------------------------------

_FRACTION_PREFIX = "flooded_fraction_2m_"
_AREA_PREFIX = "flooded_area_m2_2m_"


def flooded_fraction_col(depth_cm: int) -> str:
    """Column name for the flooded fraction in the 2 m buffer at *depth_cm* cm."""
    return f"{_FRACTION_PREFIX}{depth_cm}cm"


def flooded_area_col(depth_cm: int) -> str:
    """Column name for the flooded area [m²] in the 2 m buffer at *depth_cm* cm."""
    return f"{_AREA_PREFIX}{depth_cm}cm"


# ---------------------------------------------------------------------------
# Step 4.1 — Spatial preprocessing
# ---------------------------------------------------------------------------


def compute_wb_flood_fractions(
    buildings: gpd.GeoDataFrame,
    flood_layers: gpd.GeoDataFrame,
    params: HazardParams | None = None,
) -> pd.DataFrame:
    """Compute the flooded fraction within the 2 m building buffer per depth class.

    For each unique ``level_cm`` value found in *flood_layers* the function:

    1. Filters flood features to that depth class.
    2. Buffers each building outward by ``buffer_building_m`` (default 2 m).
    3. Erases the building footprint from the buffer (OUTSIDE_ONLY equivalent).
    4. Tabultes the intersection with the filtered flood features:
       flooded area and fraction per building.

    Parameters
    ----------
    buildings:
        GeoDataFrame of building footprint polygons.  Must contain a column
        ``id`` (or use the index as building identifier).  Must be in a
        **projected** (metric) CRS.
    flood_layers:
        Combined GeoDataFrame of flooded-area polygons for all depth classes.
        Each feature must carry a ``level_cm`` property (integer, e.g. 30).
    params:
        ``HazardParams`` controlling the 2 m buffer radius.  Defaults applied
        when ``None``.

    Returns
    -------
    pd.DataFrame
        One row per building (index = building id).  For each depth class *d*
        found in *flood_layers* the DataFrame contains two columns:
        ``flooded_fraction_2m_{d}cm`` (fraction ∈ [0, 1]) and
        ``flooded_area_m2_2m_{d}cm`` (area in m²).
        Buildings with no intersection for a depth class receive 0.0.

    Raises
    ------
    ValueError
        If ``flood_layers`` contains no ``level_cm`` column.
    """
    if params is None:
        params = HazardParams()

    if "level_cm" not in flood_layers.columns:
        raise ValueError(
            "'flood_layers' must contain a 'level_cm' column identifying the "
            "water-depth class of each feature."
        )

    # ------------------------------------------------------------------
    # Resolve building id
    # ------------------------------------------------------------------
    bld = buildings.copy()
    if "id" in bld.columns:
        bld = bld.set_index("id")
    building_id_col = "_building_id"
    bld[building_id_col] = bld.index

    all_ids = bld.index

    # ------------------------------------------------------------------
    # Pre-compute the 2 m outside-only buffer for all buildings at once.
    # (All depth classes use the same buffer geometry — compute once.)
    # ------------------------------------------------------------------
    bu2 = bld[[building_id_col, "geometry"]].copy()
    bu2["geometry"] = bu2.geometry.buffer(params.buffer_building_m)

    # Erase building footprints (OUTSIDE_ONLY equivalent)
    buildings_union = gpd.GeoDataFrame(
        geometry=[unary_union(bld.geometry)],
        crs=bld.crs,
    )
    bu2 = gpd.overlay(bu2, buildings_union, how="difference")

    # Pre-compute zone area per building (same for all depth classes)
    bu2["zone_area_m2"] = bu2.geometry.area
    zone_area = bu2.groupby(building_id_col)["zone_area_m2"].sum()

    # ------------------------------------------------------------------
    # Iterate over depth classes
    # ------------------------------------------------------------------
    depth_classes = sorted(flood_layers["level_cm"].dropna().unique().astype(int))
    logger.debug(
        "WB step 4.1: %d buildings, depth classes %s",
        len(bld),
        depth_classes,
    )

    result = pd.DataFrame(index=all_ids)
    result.index.name = "id"

    for depth_cm in depth_classes:
        _t_depth = time.perf_counter()
        logger.debug("WB step 4.1 depth=%dcm: start overlay", depth_cm)
        flood_at_depth = flood_layers[flood_layers["level_cm"] == depth_cm].copy()

        flood_union = gpd.GeoDataFrame(
            geometry=[unary_union(flood_at_depth.geometry)],
            crs=flood_layers.crs,
        )

        # Intersect zone buffer with this depth's flood layer
        flooded = gpd.overlay(
            bu2[[building_id_col, "geometry"]],
            flood_union,
            how="intersection",
        )

        frac_col = flooded_fraction_col(depth_cm)
        area_col = flooded_area_col(depth_cm)

        if flooded.empty:
            result[area_col] = 0.0
            result[frac_col] = 0.0
            continue

        flooded["flooded_area_m2"] = flooded.geometry.area
        flooded_sum = flooded.groupby(building_id_col)["flooded_area_m2"].sum()

        # Fraction = flooded / zone_area; guard zero-area zones
        tab = (
            pd.DataFrame({"flooded_area_m2": flooded_sum})
            .join(zone_area, how="right")
            .fillna(0.0)
        )
        tab[frac_col] = np.where(
            tab["zone_area_m2"] > 0.0,
            tab["flooded_area_m2"] / tab["zone_area_m2"],
            0.0,
        )
        tab = tab.rename(columns={"flooded_area_m2": area_col})

        result[area_col] = tab[area_col].reindex(all_ids, fill_value=0.0)
        result[frac_col] = tab[frac_col].reindex(all_ids, fill_value=0.0)
        logger.debug(
            "WB step 4.1 depth=%dcm: done in %.2fs",
            depth_cm,
            time.perf_counter() - _t_depth,
        )

    return result


# ---------------------------------------------------------------------------
# Step 4.2 — Hazard index (pure pandas / scipy — no spatial dependency)
# ---------------------------------------------------------------------------


def compute_hazard_wb(
    df: pd.DataFrame,
    params: HazardParams | None = None,
) -> pd.Series:
    """Compute the well-being hazard index HWB (Step 4.2, Eq. 12).

    .. math::

        H_{WB} = \\sum_{d} \\text{lognorm.cdf}(p_{2m,d} \\times 4,\\; \\sigma)

    where the sum runs over all depth classes *d* detected from the
    ``flooded_fraction_2m_{d}cm`` columns of *df*, and μ is fixed at 0.

    Parameters
    ----------
    df:
        DataFrame containing the intermediate columns produced by
        ``compute_wb_flood_fractions``.  Fraction columns are detected
        automatically by the prefix ``"flooded_fraction_2m_"``.
    params:
        Hazard parameters.  Defaults applied when ``None``.

    Returns
    -------
    pd.Series
        ``hazard_well_being`` — HWB ≥ 0 per building.  Zero where all
        flooded fractions are zero.  Unlike HMA the upper bound for HWB
        equals the number of depth classes (each fully flooded class
        contributes lognorm.cdf(4, σ) ≈ 1 at σ = 0.25).
    """
    if params is None:
        params = HazardParams()

    frac_cols = [c for c in df.columns if c.startswith(_FRACTION_PREFIX)]

    if not frac_cols:
        return pd.Series(0.0, index=df.index, name=HAZARD_WELL_BEING)

    # Stack fraction columns → (n_buildings × n_depths) array
    frac_matrix = np.column_stack([df[c].to_numpy(dtype=float) for c in frac_cols])

    # Eq. 12: HWB = Σ lognorm.cdf(fraction × 4, σ)
    hwb = lognorm.cdf(frac_matrix * 4.0, s=params.lognormal_shape).sum(axis=1)

    return pd.Series(hwb, index=df.index, name=HAZARD_WELL_BEING)


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def run_hazard_wb_pipeline(
    buildings: gpd.GeoDataFrame,
    flood_layers: gpd.GeoDataFrame,
    params: HazardParams | None = None,
) -> pd.DataFrame:
    """Run the full well-being hazard pipeline (Steps 4.1–4.2).

    Calls ``compute_wb_flood_fractions`` (Step 4.1) followed by
    ``compute_hazard_wb`` (Step 4.2) and returns a DataFrame with all
    intermediate columns plus the final index.

    Parameters
    ----------
    buildings:
        Projected GeoDataFrame of building footprints.
    flood_layers:
        Combined GeoDataFrame of flooded-area polygons with ``level_cm`` column.
    params:
        Hazard parameters.  Defaults applied when ``None``.

    Returns
    -------
    pd.DataFrame
        Columns: ``flooded_fraction_2m_{d}cm`` and ``flooded_area_m2_2m_{d}cm``
        for every depth class *d*, plus ``hazard_well_being``.
        Index matches the input building index.
    """
    if params is None:
        params = HazardParams()

    logger.debug(
        "WB pipeline start: %d buildings, %d flood features",
        len(buildings),
        len(flood_layers),
    )
    _t0 = time.perf_counter()
    result = compute_wb_flood_fractions(buildings, flood_layers, params)
    logger.debug("WB step 4.1 complete: %.2fs", time.perf_counter() - _t0)

    _t1 = time.perf_counter()
    result[HAZARD_WELL_BEING] = compute_hazard_wb(result, params)
    logger.debug(
        "WB step 4.2 complete: %.2fs — total pipeline: %.2fs",
        time.perf_counter() - _t1,
        time.perf_counter() - _t0,
    )
    return result
