"""Aggregation helpers (HEALPix, Thiessen) for pluvial_flood_risk.

This module provides spatial aggregation functions used to derive
summary outputs from building-level risk results:

Functions
---------
aggregate_to_healpix(buildings_gdf, depth=15, risk_columns=None,
                     agg_func='mean', min_buildings=1) -> GeoDataFrame
    Aggregate building-level columns into HEALPix cells (EPSG:4326).

create_thiessen_polygons(buildings_gdf, boundary_buffer_m=200,
                         simplify_tolerance=10) -> GeoDataFrame
    Replace building footprint geometries with Thiessen (Voronoi) polygons
    clipped to the convex hull of the study area.  Requires a projected
    (metric) input CRS.
"""

from __future__ import annotations

import logging
from typing import Iterable

import geopandas as gpd
import healpix_geo.nested as hpx
import numpy as np
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union, voronoi_diagram
from shapely.strtree import STRtree

from ..models.field_names import (
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
)

logger = logging.getLogger(__name__)

ELLIPSOID = "WGS84"

# Thiessen polygon defaults
_THIESSEN_BOUNDARY_BUFFER_M: float = 200.0
_THIESSEN_SIMPLIFY_TOLERANCE: float = 10.0  # metres


def aggregate_to_healpix(
    buildings_gdf: gpd.GeoDataFrame,
    depth: int = 15,
    risk_columns: Iterable[str] | None = None,
    agg_func: str | dict = "mean",
    min_buildings: int = 1,
) -> gpd.GeoDataFrame:
    """Aggregate building-level results to HEALPix cells on WGS84.

    The function mirrors the behaviour in the urban_pfr reference:
    - Assign each building centroid to a HEALPix cell (lon/lat on WGS84)
    - Aggregate the requested columns per cell (default: risk/hazard cols)
    - Apply a minimum-building count privacy filter
    - Return a GeoDataFrame of HEALPix polygons in EPSG:4326
    """
    if risk_columns is None:
        default_cols = [
            PLUVIAL_FLOOD_RISK_WELL_BEING,
            PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
            HAZARD_WELL_BEING,
            HAZARD_MOBILITY_ACCESSIBILITY,
        ]
        risk_columns = [c for c in default_cols if c in buildings_gdf.columns]

    risk_columns = list(risk_columns)
    if not risk_columns:
        raise ValueError("No risk columns found to aggregate to HEALPix")

    # Work on a copy and ensure geographic coordinates (lon/lat)
    bldg = buildings_gdf.copy()
    if bldg.crs and str(bldg.crs) != "EPSG:4326":
        bldg = bldg.to_crs("EPSG:4326")

    # Compute centroids lon/lat
    centroids = bldg.geometry.centroid
    lon = centroids.x.values
    lat = centroids.y.values

    # Map to HEALPix cell ids
    pixels = hpx.lonlat_to_healpix(lon, lat, depth, ellipsoid=ELLIPSOID)
    bldg["_healpix"] = pixels

    # Build aggregation dict
    agg_dict = {col: (agg_func if isinstance(agg_func, str) else agg_func.get(col, "mean"))
                for col in risk_columns}
    agg_dict["_healpix"] = "count"

    grouped = bldg.groupby("_healpix").agg(agg_dict)
    # Rename the count column
    grouped = grouped.rename(columns={"_healpix": "n_buildings"})

    # Privacy filter
    grouped = grouped[grouped["n_buildings"] >= min_buildings]

    # Create HEALPix polygons
    cell_ids = np.array(grouped.index, dtype=np.uint64)
    vlon, vlat = hpx.vertices(cell_ids, depth, ellipsoid=ELLIPSOID)

    geometries = []
    for i in range(len(cell_ids)):
        coords = list(zip(vlon[i], vlat[i]))
        coords.append(coords[0])
        geometries.append(Polygon(coords))

    result = gpd.GeoDataFrame(
        grouped.reset_index(),
        geometry=geometries,
        crs="EPSG:4326",
    )
    # Provide a stable column name
    if "_healpix" in result.columns:
        result = result.rename(columns={"_healpix": "healpix_id"})

    return result


# ---------------------------------------------------------------------------
# Thiessen (Voronoi) polygon tessellation
# ---------------------------------------------------------------------------


def create_thiessen_polygons(
    buildings_gdf: gpd.GeoDataFrame,
    boundary_buffer_m: float = _THIESSEN_BOUNDARY_BUFFER_M,
    simplify_tolerance: float = _THIESSEN_SIMPLIFY_TOLERANCE,
) -> gpd.GeoDataFrame:
    """Replace building footprint geometries with Thiessen (Voronoi) polygons.

    Each building is represented by the Voronoi cell of its centroid, clipped
    to the study-area boundary (buffered convex hull of all input buildings).
    All non-geometry columns — including computed risk values — are preserved
    unchanged.

    The function is designed for cartographic output: Voronoi polygons fill
    the study area continuously, avoiding the visual gaps that appear when
    building footprints are rendered at small scales.

    Parameters
    ----------
    buildings_gdf:
        Building-level GeoDataFrame in a **projected (metric) CRS**.  Must
        contain at least two features with non-empty geometries.
    boundary_buffer_m:
        Buffer distance [m] added around the convex hull of all building
        footprints before the Voronoi envelope is computed.  Controls how far
        the outer Voronoi cells extend beyond the outermost buildings.
        Default: 200 m.
    simplify_tolerance:
        Douglas-Peucker tolerance [m] applied to the clip boundary before the
        intersection step.  Reduces vertex count without perceptible distortion
        of the clipped polygons.  Default: 10 m.

    Returns
    -------
    GeoDataFrame
        Same index, columns, and CRS as *buildings_gdf*, but with Thiessen
        polygon geometries instead of building footprints.  Rows whose clipped
        polygon is empty are silently dropped.

    Raises
    ------
    ValueError
        If *buildings_gdf* is not in a projected CRS, or if fewer than two
        features with valid centroids are present.
    """
    if buildings_gdf.crs is None or not buildings_gdf.crs.is_projected:
        raise ValueError(
            "create_thiessen_polygons requires a projected (metric) CRS. "
            f"Got: {buildings_gdf.crs!r}."
        )

    n_total = len(buildings_gdf)
    logger.info("Thiessen: starting tessellation for %d buildings", n_total)

    # --- Compute centroids and filter invalid geometries ---
    centroids = buildings_gdf.geometry.centroid
    valid_mask = ~centroids.is_empty & centroids.notna()
    n_invalid = int((~valid_mask).sum())
    if n_invalid:
        logger.warning(
            "Thiessen: %d building(s) have empty/null centroids and will be skipped",
            n_invalid,
        )
    centroids_valid = centroids[valid_mask]
    n_valid = len(centroids_valid)
    if n_valid < 2:
        raise ValueError(
            "create_thiessen_polygons requires at least 2 buildings with valid "
            f"centroids; got {n_valid} after filtering."
        )

    # --- Clip boundary: buffered convex hull of all footprints ---
    union_footprints = unary_union(buildings_gdf.geometry)
    envelope = union_footprints.convex_hull.buffer(boundary_buffer_m)
    clip_boundary = envelope.simplify(simplify_tolerance)
    logger.debug(
        "Thiessen: clip boundary %.2f km², %d vertices (simplified from %d)",
        clip_boundary.area / 1_000_000,
        _count_coords(clip_boundary),
        _count_coords(envelope),
    )

    # --- Voronoi diagram from valid centroids ---
    x = np.asarray(centroids_valid.x)
    y = np.asarray(centroids_valid.y)
    multipoint = MultiPoint(np.column_stack([x, y]))
    # Expand envelope so edge cells are not clipped by the diagram bounding box
    voronoi_envelope = clip_boundary.buffer(boundary_buffer_m)
    voronoi = voronoi_diagram(multipoint, envelope=voronoi_envelope)
    voronoi_cells = list(voronoi.geoms)
    logger.debug(
        "Thiessen: Voronoi produced %d cells for %d centroids",
        len(voronoi_cells),
        n_valid,
    )

    # --- Match each centroid to its nearest Voronoi cell (vectorised) ---
    tree = STRtree(voronoi_cells)
    nearest_idx = tree.nearest(np.asarray(centroids_valid))  # shape (n_valid,), dtype int
    matched_cells = [voronoi_cells[int(i)] for i in nearest_idx]

    # --- Clip cells to study-area boundary ---
    n_inside = n_clipped = n_outside = 0
    clipped_cells = []
    for cell in matched_cells:
        if clip_boundary.contains(cell):
            clipped_cells.append(cell)
            n_inside += 1
        elif clip_boundary.intersects(cell):
            clipped_cells.append(cell.intersection(clip_boundary))
            n_clipped += 1
        else:
            # Cell lies entirely outside the clip boundary — keep raw so the
            # empty-geometry filter below removes it gracefully.
            clipped_cells.append(cell)
            n_outside += 1
    logger.debug(
        "Thiessen: %d cells inside, %d clipped, %d outside boundary",
        n_inside,
        n_clipped,
        n_outside,
    )

    # --- Assemble result GeoDataFrame ---
    result = buildings_gdf[valid_mask].copy()
    result = result.set_geometry(
        gpd.GeoSeries(clipped_cells, index=result.index, crs=buildings_gdf.crs)
    )
    result = result[~result.geometry.is_empty & result.geometry.notna()]

    n_out = len(result)
    n_dropped = n_total - n_out
    if n_dropped:
        logger.warning(
            "Thiessen: %d of %d buildings dropped (empty polygon after clipping)",
            n_dropped,
            n_total,
        )

    logger.info(
        "Thiessen: complete — %d polygons, median area=%.0f m², total=%.2f km²",
        n_out,
        result.geometry.area.median(),
        result.geometry.area.sum() / 1_000_000,
    )
    return result


def _count_coords(geom) -> int:
    """Count total coordinate pairs in a Shapely geometry (recursively)."""
    if hasattr(geom, "geoms"):
        return sum(_count_coords(g) for g in geom.geoms)
    if hasattr(geom, "exterior"):
        return len(geom.exterior.coords) + sum(
            len(ring.coords) for ring in geom.interiors
        )
    if hasattr(geom, "coords"):
        return len(geom.coords)
    return 0
