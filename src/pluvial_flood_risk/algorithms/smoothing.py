"""Module 5.5 — Optional Delaunay-neighbour risk smoothing.

Reimplements the algorithm owners' legacy "optional smoothing tool"
(``scratch/arcpy-pipeline scripts/optional-smoothing-*.py``), fixing its
known bugs while preserving its core idea: blend each building's risk value
with its Delaunay-triangulation neighbours, weighted by inverse distance.

Fixes relative to the legacy script:

- **Normalisation**: the legacy formula divided by
  ``len(connections[i]) + 1`` regardless of how many neighbours actually
  contributed a value, shrinking values towards zero with every iteration.
  This implementation divides by the sum of weights actually used (own
  weight 1 + each contributing neighbour's ``1/distance``), giving a true
  weighted average.
- **NULL leakage**: the legacy script substituted 0 for missing values
  before averaging, pulling every neighbour's smoothed value down. Here,
  buildings with a NaN risk value are skipped both as a smoothing target
  and as a contributing neighbour.
- **Neighbour proximity**: Delaunay triangulation alone can pair buildings
  that are geometrically distant (e.g. across the convex hull, or with no
  real hydraulic connection). A ``max_distance_m`` cutoff discards any
  Delaunay edge longer than this before the weighted average is computed.
- **Coordinate system**: distances are computed on projected (metric)
  coordinates, not raw latitude/longitude.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import Delaunay, QhullError

from ..models.field_names import (
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
)
from ..models.inputs import SmoothingParams

logger = logging.getLogger(__name__)

_SMOOTHED_COLUMNS = (
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
)


def _delaunay_neighbors(coords: np.ndarray) -> list[list[int]]:
    """Return an adjacency list built from the Delaunay triangulation of *coords*."""
    tri = Delaunay(coords)
    neighbors: list[list[int]] = [[] for _ in range(len(coords))]
    for simplex in tri.simplices:
        for i in range(3):
            for j in range(i + 1, 3):
                p1, p2 = int(simplex[i]), int(simplex[j])
                if p2 not in neighbors[p1]:
                    neighbors[p1].append(p2)
                if p1 not in neighbors[p2]:
                    neighbors[p2].append(p1)
    return neighbors


def _smooth_pass(
    values: np.ndarray,
    coords: np.ndarray,
    neighbors: list[list[int]],
    max_distance_m: float,
) -> np.ndarray:
    """Run one inverse-distance-weighted smoothing pass, NaN-safe.

    A building's own value always carries weight 1; each neighbour within
    ``max_distance_m`` contributes weight ``1 / distance``. The result is
    divided by the sum of weights actually used, so it is a true weighted
    average rather than the legacy script's fixed-denominator average.
    """
    result = values.copy()
    for i, value in enumerate(values):
        if np.isnan(value):
            continue
        weighted_sum = value
        weight_total = 1.0
        for j in neighbors[i]:
            neighbor_value = values[j]
            if np.isnan(neighbor_value):
                continue
            distance = float(np.linalg.norm(coords[j] - coords[i]))
            if distance <= 0 or distance > max_distance_m:
                continue
            weight = 1.0 / distance
            weighted_sum += neighbor_value * weight
            weight_total += weight
        result[i] = weighted_sum / weight_total
    return result


def smooth_risk_values(
    risk_df: pd.DataFrame,
    buildings_metric_gdf: gpd.GeoDataFrame,
    params: SmoothingParams,
) -> pd.DataFrame:
    """Smooth PFR_WB/PFR_MA columns of *risk_df* using Delaunay neighbours.

    No-op (returns *risk_df* unchanged) unless ``params.enabled`` is True.

    Args:
        risk_df: DataFrame indexed by building id (str), containing the
            well-being and/or mobility-accessibility risk columns.
        buildings_metric_gdf: Building GeoDataFrame in a projected (metric)
            CRS, indexed by the same building ids. Only geometry (centroid)
            is used.
        params: Smoothing configuration.

    Returns:
        A copy of *risk_df* with the smoothed risk columns replaced.
        Buildings without matching geometry, or with a NaN risk value, are
        left unchanged.
    """
    if not params.enabled:
        return risk_df

    common_ids = risk_df.index.intersection(buildings_metric_gdf.index)
    if len(common_ids) < 3:
        logger.warning(
            "smooth_risk_values: fewer than 3 buildings with matching "
            "geometry (%d) — skipping smoothing (Delaunay needs >= 3 points)",
            len(common_ids),
        )
        return risk_df

    centroids = buildings_metric_gdf.loc[common_ids].geometry.centroid
    coords = np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()])

    try:
        neighbors = _delaunay_neighbors(coords)
    except QhullError:
        logger.warning(
            "smooth_risk_values: Delaunay triangulation failed (degenerate "
            "geometry, e.g. collinear points) — skipping smoothing"
        )
        return risk_df

    out = risk_df.copy()
    for column in _SMOOTHED_COLUMNS:
        if column not in out.columns:
            continue
        values = out.loc[common_ids, column].to_numpy(dtype=float)
        for _ in range(params.iterations):
            values = _smooth_pass(values, coords, neighbors, params.max_distance_m)
        out.loc[common_ids, column] = values

    logger.info(
        "smooth_risk_values: smoothed %d buildings over %d iteration(s), "
        "max_distance_m=%.1f",
        len(common_ids), params.iterations, params.max_distance_m,
    )
    return out
