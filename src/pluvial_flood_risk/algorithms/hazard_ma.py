"""Hazard pipeline — Module 3: Mobility & Accessibility (Steps 3.1–3.2).

Implements the concentric buffer spatial intersection and the log-normal CDF
hazard index following Vogelbacher et al. (2025), v2 reference scripts:
    3_Step1_Calculating_the_Hazard_for_Mobility_and_Accessability_v2.py
    3_Step2_Calculating_the_Hazard_for_Mobility_and_Accessability_v2.py

Pipeline sequence
-----------------
Step 3.1  compute_mobility_flood_fractions()  → 6 intermediate columns
Step 3.2  compute_hazard_ma()                 → hazard_mobility_accessibility

Step 3.1 — Spatial preprocessing
----------------------------------
Three concentric zones per building are intersected with streets and the
30 cm flood layer.  Flooded fractions of each zone drive the hazard index:

    Zone 1 — 5 m direct-access buffer (building envelope only, no street clip):
        Bu5 = Buffer(buildings, 5m, OUTSIDE_ONLY)
        Tabulate intersection of Bu5 × flood_layer → flooded_area_5m, flooded_fraction_5m

    Zone 2 — Inner street zone (0–15 m from building, street-clipped):
        B15  = Buffer(buildings, 15m, OUTSIDE_ONLY)
        Bu15 = Erase(B15, buildings)           # remove building footprint
        BuS15 = Intersect(streets, Bu15)       # clip to street polygons
        Tabulate intersection of BuS15 × flood_layer → flooded_area_15m, flooded_fraction_15m

    Zone 3 — Outer street zone (15–30 m ring from building, street-clipped):
        B1530 = MultipleRingBuffer(B15, ring_width)   # ring outside B15
        BuS30 = Intersect(streets, B1530)
        Tabulate intersection of BuS30 x flood_layer -> flooded_area_30m, flooded_fraction_30m

The v2 algorithm uses a single street layer (v1 used two) and a single flood
layer for both street zones (v1 used separate flood layers per zone).

Step 3.2 — Hazard index (Eq. 13)
----------------------------------
    HMA = lognorm.cdf( max(p_5m, p_15m_filtered, p_30m_filtered) × 4, σ )

where each p is the flooded fraction [0, 1] in that zone, and the min-area
filter suppresses noise from small street intersections (< min_area_m2).

Open-source equivalents of the arcpy operations
------------------------------------------------
| arcpy                  | geopandas / shapely                             |
|------------------------|-------------------------------------------------|
| Buffer(OUTSIDE_ONLY)   | gdf.buffer(r).difference(gdf.union_all())       |
| Erase(A, B)            | gpd.overlay(A, B, how='difference')             |
| Intersect(A, B)        | gpd.overlay(A, B, how='intersection')           |
| TabulateIntersection   | overlay → .area, then groupby('building_id').sum|
| MultipleRingBuffer     | outer_buffer.difference(inner_buffer)           |

Architecture note
-----------------
``compute_mobility_flood_fractions`` is a spatial function — it requires
GeoDataFrames from geopandas.  ``compute_hazard_ma`` operates on plain
pandas Series (flooded fractions) and has no spatial dependencies; it can
therefore be tested without any geometry.

Both are called by ``run_hazard_ma_pipeline``, which orchestrates the full
module and is the function referenced in ``flood_risk_process.py``.
"""

from __future__ import annotations

import logging
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.stats import lognorm
from shapely.ops import unary_union

from ..models.field_names import (
    FLOODED_AREA_M2_5M,
    FLOODED_AREA_M2_15M,
    FLOODED_AREA_M2_30M,
    FLOODED_FRACTION_5M,
    FLOODED_FRACTION_15M,
    FLOODED_FRACTION_30M,
    HAZARD_MOBILITY_ACCESSIBILITY,
)
from ..models.inputs import HazardParams

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step 3.1 — Spatial preprocessing
# ---------------------------------------------------------------------------


def compute_mobility_flood_fractions(
    buildings: gpd.GeoDataFrame,
    streets: gpd.GeoDataFrame,
    flood: gpd.GeoDataFrame,
    params: HazardParams | None = None,
) -> pd.DataFrame:
    """Compute flooded area and fraction for each of the three mobility zones.

    The function follows the v2 reference algorithm exactly:

    1. Zone 1 (direct access, 5 m buffer, no street clip):
       Buffer building footprints outward by ``buffer_zone1_m``, erase the
       building itself, then tabulate the intersection with the flood layer.

    2. Zone 2 (inner street zone, 0–15 m from building):
       Buffer by ``buffer_zone2_m``, erase buildings → clip to streets →
       tabulate intersection with flood layer.

    3. Zone 3 (outer street ring, 15–(15+ring_width) m from building):
       Build a ring = Buffer(B15, ring_width) minus B15 -> clip to streets ->
       tabulate intersection with flood layer.

    Parameters
    ----------
    buildings:
        GeoDataFrame of building footprint polygons.  Must contain a column
        ``id`` (or use the index as building identifier).  Must be in a
        **projected** (metric) CRS — EPSG:4326 will produce wrong areas.
    streets:
        GeoDataFrame of street geometries (lines or polygons).  Buffered to a
        thin polygon (``street_buffer_tolerance_m = 0.5 m``) when the
        geometries are LineStrings so that intersections produce non-zero areas.
    flood:
        GeoDataFrame of flooded area polygons for the 30 cm scenario.
    params:
        ``HazardParams`` controlling buffer radii.  Defaults applied when
        ``None``.

    Returns
    -------
    pd.DataFrame
        One row per building (index = building id) with columns:
        ``flooded_area_m2_5m``, ``flooded_fraction_5m``,
        ``flooded_area_m2_15m``, ``flooded_fraction_15m``,
        ``flooded_area_m2_30m``, ``flooded_fraction_30m``.
        Buildings with no intersection in a zone receive 0.0 in both columns.
    """
    if params is None:
        params = HazardParams()

    # ------------------------------------------------------------------
    # Geometry diagnostics (DEBUG — helps diagnose vertex-complexity issues)
    # ------------------------------------------------------------------
    def _geo_diag(gdf: gpd.GeoDataFrame, name: str) -> None:
        n_coords = int(shapely.get_num_coordinates(gdf.geometry.values).sum())
        n_invalid = int((~gdf.geometry.is_valid).sum())
        logger.debug(
            "MA geometry diagnostic — %s: %d features, %d total coords, "
            "%d invalid geometries",
            name, len(gdf), n_coords, n_invalid,
        )

    _geo_diag(buildings, "buildings")
    _geo_diag(streets, "streets")
    _geo_diag(flood, "flood")

    # Per-building vertex count distribution — key indicator of GEOS cost
    _bld_coords = shapely.get_num_coordinates(buildings.geometry.values)
    logger.debug(
        "MA buildings vertex count: min=%d p50=%d p95=%d p99=%d max=%d",
        int(_bld_coords.min()),
        int(np.percentile(_bld_coords, 50)),
        int(np.percentile(_bld_coords, 95)),
        int(np.percentile(_bld_coords, 99)),
        int(_bld_coords.max()),
    )

    # ------------------------------------------------------------------
    # Resolve building id column
    # ------------------------------------------------------------------
    bld = buildings.copy()
    if "id" in bld.columns:
        bld = bld.set_index("id")
    building_id_col = "_building_id"
    bld[building_id_col] = bld.index

    # ------------------------------------------------------------------
    # Dissolve flood into a single geometry for fast difference operations
    # ------------------------------------------------------------------
    _t_fu = time.perf_counter()
    _flood_union_geom = unary_union(flood.geometry)
    logger.debug(
        "MA flood_union: %s, %d coords, built in %.2fs",
        _flood_union_geom.geom_type,
        shapely.get_num_coordinates(_flood_union_geom),
        time.perf_counter() - _t_fu,
    )
    flood_union: gpd.GeoDataFrame = gpd.GeoDataFrame(
        geometry=[_flood_union_geom],
        crs=flood.crs,
    )

    # ------------------------------------------------------------------
    # Widen line streets to thin polygons (0.5 m half-width) so that
    # intersection produces non-zero areas.
    # ------------------------------------------------------------------
    streets_poly = streets.copy()
    if streets_poly.geom_type.isin(["LineString", "MultiLineString"]).any():
        streets_poly["geometry"] = streets_poly.geometry.buffer(0.5)

    # ------------------------------------------------------------------
    # Normalise building geometry before buffering/erase:
    #   1. make_valid — fixes self-intersections that explode GEOS diff cost
    #   2. simplify(0.1 m) — removes sub-decimetre vertices that inflate
    #      vertex counts without affecting the 5/15/30 m buffer areas
    # Both are cheap no-ops when geometries are already clean.
    # ------------------------------------------------------------------
    _bld_crs = bld.crs
    _bld_geom_raw = bld.geometry.values
    _bld_geom_valid = shapely.make_valid(_bld_geom_raw)
    _bld_arr = shapely.simplify(_bld_geom_valid, tolerance=0.1, preserve_topology=True)
    bld = bld.set_geometry(
        gpd.GeoSeries(_bld_arr, index=bld.index, crs=_bld_crs)
    )
    logger.debug(
        "MA buildings after make_valid+simplify: "
        "coords before=%d, after=%d",
        int(shapely.get_num_coordinates(_bld_geom_raw).sum()),
        int(shapely.get_num_coordinates(_bld_arr).sum()),
    )

    # ------------------------------------------------------------------
    # STRtree for building footprint erase (replaces city-wide unary_union
    # which is slow when buildings overlap — subtract only nearby footprints
    # from each zone buffer instead of the full-city geometry).
    # ------------------------------------------------------------------
    _bld_arr = bld.geometry.values
    _bld_ids = bld[building_id_col].values
    _bld_strtree = shapely.STRtree(_bld_arr)

    _left, _right = _bld_strtree.query(_bld_arr, predicate="overlaps")
    _n_overlapping_pairs = int((_left < _right).sum())
    if _n_overlapping_pairs > 0:
        logger.warning(
            "MA buildings: %d overlapping building pairs detected — "
            "using STRtree-local erase to avoid GEOS complexity.",
            _n_overlapping_pairs,
        )
    else:
        logger.debug("MA buildings: 0 overlapping pairs")

    def _erase_bld(buffers_arr: np.ndarray, zone_label: str = "") -> gpd.GeoDataFrame:
        """Subtract locally-intersecting building footprints from each buffer.

        Uses the pre-built STRtree so only nearby buildings are erased from
        each zone, avoiding a city-wide unary_union.
        """
        zone_idx, hit_idx = _bld_strtree.query(
            buffers_arr, predicate="intersects"
        )
        unique_zones = np.unique(zone_idx)
        hits_per_zone = np.bincount(zone_idx, minlength=len(buffers_arr))
        logger.debug(
            "MA _erase_bld %s: %d zones with hits, "
            "neighbors/building: min=%d p50=%d p95=%d max=%d",
            zone_label,
            len(unique_zones),
            int(hits_per_zone[hits_per_zone > 0].min()),
            int(np.median(hits_per_zone[hits_per_zone > 0])),
            int(np.percentile(hits_per_zone[hits_per_zone > 0], 95)),
            int(hits_per_zone.max()),
        )
        result = buffers_arr.copy()
        for zi in unique_zones:
            mask = zone_idx == zi
            result[zi] = shapely.difference(
                buffers_arr[zi],
                shapely.unary_union(_bld_arr[hit_idx[mask]]),
            )
        valid = ~shapely.is_empty(result)
        return gpd.GeoDataFrame(
            {building_id_col: _bld_ids[valid], "geometry": result[valid]},
            crs=bld.crs,
        )

    # ------------------------------------------------------------------
    # helper: tabulate flooded fraction for each building in a zone GDF
    # ------------------------------------------------------------------
    def _tabulate(zone_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
        """Intersect zone polygons with flood and sum area per building.

        Parameters
        ----------
        zone_gdf:
            GeoDataFrame carrying ``_building_id`` for each zone polygon.

        Returns
        -------
        DataFrame with columns ``flooded_area_m2`` and ``flooded_fraction``,
        indexed by building id.  Buildings absent from the intersection get 0.
        """
        _empty = pd.DataFrame(
            {
                "flooded_area_m2": pd.Series(dtype=float),
                "flooded_fraction": pd.Series(dtype=float),
            },
        )
        if zone_gdf.empty:
            return _empty

        zone_gdf = zone_gdf[[building_id_col, "geometry"]].copy()

        # Compute zone area per building before flood intersection
        zone_gdf["zone_area_m2"] = zone_gdf.geometry.area
        zone_area = zone_gdf.groupby(building_id_col)["zone_area_m2"].sum()

        # Intersect zone polygons with the flood layer
        flooded = gpd.overlay(zone_gdf, flood_union, how="intersection", keep_geom_type=False)
        if flooded.empty:
            return _empty

        flooded["flooded_area_m2"] = flooded.geometry.area
        flooded_sum = flooded.groupby(building_id_col)["flooded_area_m2"].sum()

        result = pd.DataFrame({"flooded_area_m2": flooded_sum})
        result = result.join(zone_area, how="right").fillna(0.0)
        # Fraction: flooded / zone_total; guard against degenerate zero area
        result["flooded_fraction"] = np.where(
            result["zone_area_m2"] > 0.0,
            result["flooded_area_m2"] / result["zone_area_m2"],
            0.0,
        )
        return result[["flooded_area_m2", "flooded_fraction"]]

    # ------------------------------------------------------------------
    # Zone 1 — 5 m direct-access buffer (no street clip)
    # ------------------------------------------------------------------
    logger.info(
        "MA step 3.1 zone1: buffer=%.1fm, %d buildings",
        params.buffer_zone1_m,
        len(bld),
    )
    _t_z1 = time.perf_counter()
    _b5_arr = shapely.buffer(_bld_arr, params.buffer_zone1_m)
    bu5 = _erase_bld(_b5_arr, "zone1")
    tab5 = _tabulate(bu5)
    logger.info("MA step 3.1 zone1: done in %.2fs", time.perf_counter() - _t_z1)

    # ------------------------------------------------------------------
    # Zone 2 — 0–15 m street zone
    # ------------------------------------------------------------------
    logger.info(
        "MA step 3.1 zone2: buffer=%.1fm, %d streets",
        params.buffer_zone2_m,
        len(streets_poly),
    )
    _t_z2 = time.perf_counter()
    _b15_arr = shapely.buffer(_bld_arr, params.buffer_zone2_m)
    bu15 = _erase_bld(_b15_arr, "zone2")
    # Clip to street polygons
    bus15 = gpd.overlay(streets_poly, bu15, how="intersection", keep_geom_type=False)
    tab15 = _tabulate(bus15)
    logger.info("MA step 3.1 zone2: done in %.2fs", time.perf_counter() - _t_z2)

    # ------------------------------------------------------------------
    # Zone 3 — outer street ring (B15 → B15+ring_width)
    # ------------------------------------------------------------------
    ring_width = params.buffer_zone3_m - params.buffer_zone2_m
    if ring_width <= 0.0:
        raise ValueError(
            f"buffer_zone3_m ({params.buffer_zone3_m}) must be greater than "
            f"buffer_zone2_m ({params.buffer_zone2_m}) to form a valid outer ring."
        )
    logger.info(
        "MA step 3.1 zone3: ring %.1fm–%.1fm, %d streets",
        params.buffer_zone2_m,
        params.buffer_zone3_m,
        len(streets_poly),
    )
    _t_z3 = time.perf_counter()
    # Ring = Buffer(_b15_arr, ring_width) \ _b15_arr — vectorized 1-to-1
    _b1530_arr = shapely.buffer(_b15_arr, ring_width)
    _ring_arr = shapely.difference(_b1530_arr, _b15_arr)
    _valid_ring = ~shapely.is_empty(_ring_arr)
    b1530 = gpd.GeoDataFrame(
        {building_id_col: _bld_ids[_valid_ring], "geometry": _ring_arr[_valid_ring]},
        crs=bld.crs,
    )
    # Clip to streets
    bus30 = gpd.overlay(streets_poly, b1530, how="intersection", keep_geom_type=False)
    tab30 = _tabulate(bus30)
    logger.info("MA step 3.1 zone3: done in %.2fs", time.perf_counter() - _t_z3)

    # ------------------------------------------------------------------
    # Assemble result DataFrame — all buildings present, missing → 0
    # ------------------------------------------------------------------
    all_ids = bld.index
    result = pd.DataFrame(index=all_ids)
    result.index.name = "id"

    def _fill(tab: pd.DataFrame, area_col: str, frac_col: str) -> None:
        result[area_col] = tab["flooded_area_m2"].reindex(all_ids, fill_value=0.0)
        result[frac_col] = tab["flooded_fraction"].reindex(all_ids, fill_value=0.0)

    _fill(tab5, FLOODED_AREA_M2_5M, FLOODED_FRACTION_5M)
    _fill(tab15, FLOODED_AREA_M2_15M, FLOODED_FRACTION_15M)
    _fill(tab30, FLOODED_AREA_M2_30M, FLOODED_FRACTION_30M)

    return result


# ---------------------------------------------------------------------------
# Step 3.2 — Hazard index (pure pandas / scipy — no spatial dependency)
# ---------------------------------------------------------------------------


def compute_hazard_ma(
    df: pd.DataFrame,
    params: HazardParams | None = None,
) -> pd.Series:
    """Compute the mobility & accessibility hazard index HMA (Step 3.2, Eq. 13).

    .. math::

        H_{MA} = \\text{lognorm.cdf}\\!\\left(
            \\max(p_{5m},\\; p_{15m}^*,\\; p_{30m}^*) \\times 4,\\; \\sigma
        \\right)

    where :math:`p^*` denotes the min-area-filtered fraction:

    - :math:`p_{15m}^* = 0` if ``flooded_area_m2_15m / flooded_fraction_15m × 100
      ≤ min_street_flood_area_m2`` (i.e. the road polygon is too small to be
      meaningful), otherwise :math:`p_{15m}^* = p_{15m}`.
    - Likewise for :math:`p_{30m}^*`.

    The ``× 4`` factor comes from the ArcPy scripts and effectively scales the
    fraction from the buffer zone relative to a reference zone four times
    smaller; μ is fixed at 0 and σ is ``HazardParams.lognormal_shape``.

    Parameters
    ----------
    df:
        DataFrame containing the six intermediate columns produced by
        ``compute_mobility_flood_fractions``.
    params:
        Hazard parameters.  Defaults applied when ``None``.

    Returns
    -------
    pd.Series
        ``hazard_mobility_accessibility`` — HMA ∈ [0, 1] per building.
        Zero where all flooded fractions are zero.
    """
    if params is None:
        params = HazardParams()

    area_15m = df[FLOODED_AREA_M2_15M].to_numpy(dtype=float)
    frac_15m = df[FLOODED_FRACTION_15M].to_numpy(dtype=float)
    area_30m = df[FLOODED_AREA_M2_30M].to_numpy(dtype=float)
    frac_30m = df[FLOODED_FRACTION_30M].to_numpy(dtype=float)
    frac_5m = df[FLOODED_FRACTION_5M].to_numpy(dtype=float)

    min_area = params.min_street_flood_area_m2

    # Min-area filter for street zones:
    # The original script computes:   area / percentage * 100 <= minarea
    # which is equivalent to:  zone_total_area <= minarea
    # (area / (frac*100) * 100 = area/frac = zone_total_area)
    # Suppress the fraction if the underlying street polygon is too small.
    # Use np.divide with out/where to avoid RuntimeWarning on zero denominators.
    zone_area_15m = np.divide(
        area_15m,
        frac_15m,
        out=np.zeros_like(area_15m),
        where=frac_15m > 0.0,
    )
    zone_area_30m = np.divide(
        area_30m,
        frac_30m,
        out=np.zeros_like(area_30m),
        where=frac_30m > 0.0,
    )

    # Eq. 13 filter: set to 0 when zone is present but too small
    p15_filtered = np.where(
        (area_15m > 0.0) & (zone_area_15m <= min_area), 0.0, frac_15m
    )
    p30_filtered = np.where(
        (area_30m > 0.0) & (zone_area_30m <= min_area), 0.0, frac_30m
    )

    # Eq. 13: HMA = lognorm.cdf(max(p5, p15*, p30*) * 4, σ)
    p_max = np.maximum(frac_5m, np.maximum(p15_filtered, p30_filtered))
    hma = lognorm.cdf(p_max * 4.0, s=params.lognormal_shape)

    return pd.Series(hma, index=df.index, name=HAZARD_MOBILITY_ACCESSIBILITY)


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def run_hazard_ma_pipeline(
    buildings: gpd.GeoDataFrame,
    streets: gpd.GeoDataFrame,
    flood: gpd.GeoDataFrame,
    params: HazardParams | None = None,
) -> pd.DataFrame:
    """Run the full mobility & accessibility hazard pipeline (Steps 3.1–3.2).

    Calls ``compute_mobility_flood_fractions`` (Step 3.1) followed by
    ``compute_hazard_ma`` (Step 3.2) and returns a DataFrame with all seven
    output columns.

    Parameters
    ----------
    buildings:
        Projected GeoDataFrame of building footprints.
    streets:
        GeoDataFrame of street geometries (lines or polygons).
    flood:
        GeoDataFrame of flooded area polygons (30 cm scenario).
    params:
        Hazard parameters.  Defaults applied when ``None``.

    Returns
    -------
    pd.DataFrame
        Columns: ``flooded_area_m2_5m``, ``flooded_fraction_5m``,
        ``flooded_area_m2_15m``, ``flooded_fraction_15m``,
        ``flooded_area_m2_30m``, ``flooded_fraction_30m``,
        ``hazard_mobility_accessibility``.
        Index matches the input building index.
    """
    if params is None:
        params = HazardParams()

    logger.info(
        "MA pipeline start: %d buildings, %d streets, %d flood features",
        len(buildings),
        len(streets),
        len(flood),
    )
    _t0 = time.perf_counter()
    result = compute_mobility_flood_fractions(buildings, streets, flood, params)
    logger.info("MA step 3.1 complete: %.2fs", time.perf_counter() - _t0)

    _t1 = time.perf_counter()
    result[HAZARD_MOBILITY_ACCESSIBILITY] = compute_hazard_ma(result, params)
    logger.info(
        "MA step 3.2 complete: %.2fs — total pipeline: %.2fs",
        time.perf_counter() - _t1,
        time.perf_counter() - _t0,
    )
    return result
