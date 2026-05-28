"""Aggregation helpers (HEALPix, units) for pluvial_flood_risk.

This module adopts the HEALPix aggregation logic from the urban_pfr
reference implementation and adapts it to the project's field names.

Functions
---------
aggregate_to_healpix(buildings_gdf, depth=15, risk_columns=None,
                     agg_func='mean', min_buildings=1) -> GeoDataFrame
    Aggregate building-level columns into HEALPix cells (EPSG:4326).
"""

from __future__ import annotations

import json
from typing import Iterable

import geopandas as gpd
import healpix_geo.nested as hpx
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from ..models.field_names import (
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
)

ELLIPSOID = "WGS84"


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

    nside = 2 ** depth
    cell_area_m2 = 4 * np.pi * 6371000 ** 2 / (12 * nside ** 2)

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
