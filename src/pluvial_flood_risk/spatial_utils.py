"""Spatial utility helpers shared across pipeline branches."""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping, shape

from .models.field_names import (  # noqa: E402
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

logger = logging.getLogger(__name__)
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


def convert_geojson_to_gdf(
    features: list, source_crs: str | None = None
) -> gpd.GeoDataFrame:
    """Parse a GeoJSON features list into a GeoDataFrame.

    Handles two common data quality issues transparently:

    * Features missing the ``"geometry"`` key entirely (not even ``null``) are
      normalised to ``null`` geometry so geopandas does not crash.
    * Topologically invalid geometries (self-intersecting rings, etc.) are
      repaired in-place via ``make_valid()``.

    Parameters
    ----------
    features:
        GeoJSON feature list from a FeatureCollection.
    source_crs:
        CRS to assign when no CRS is embedded in the features.  Pass
        ``params.target_crs`` whenever the input coordinates are already in a
        projected system (e.g. ``'EPSG:25832'``).  Defaults to ``'EPSG:4326'``
        for standard WGS-84 GeoJSON.
    """
    missing = sum(1 for f in features if "geometry" not in f)
    if missing:
        logger.warning(
            "convert_geojson_to_gdf: %d/%d features missing 'geometry' key; "
            "normalizing to null geometry",
            missing, len(features),
        )
        features = [
            f if "geometry" in f else {**f, "geometry": None}
            for f in features
        ]
    gdf = gpd.GeoDataFrame.from_features(features)
    if gdf.crs is None:
        gdf = gdf.set_crs(source_crs or "EPSG:4326")
    invalid_mask = gdf.geometry.notna() & ~gdf.geometry.is_valid
    if invalid_mask.any():
        logger.warning(
            "convert_geojson_to_gdf: %d/%d geometries are invalid "
            "(e.g. self-intersecting); repairing with make_valid()",
            invalid_mask.sum(), len(gdf),
        )
        gdf.geometry = gdf.geometry.make_valid()
    return gdf


def project_crs_to_metric(
    gdf: gpd.GeoDataFrame,
    target_crs: str | None,
    fallback_gdf: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Project *gdf* to a metric CRS and return the result.

    Resolution order:

    1. ``target_crs`` from params — used as-is (must be a projected CRS).
    2. Input already projected — keep its CRS unchanged.
    3. Input geographic (degrees) — auto-detect UTM zone from *gdf* centroid,
       or from *fallback_gdf* centroid when *gdf* is empty (e.g. an empty
       flood layer).

    Parameters
    ----------
    gdf:
        GeoDataFrame to project.  ``crs`` must be set.
    target_crs:
        Explicit CRS string (e.g. ``'EPSG:25832'``) or ``None``.
    fallback_gdf:
        Optional GeoDataFrame used for UTM estimation when *gdf* is empty.
    """
    if target_crs is not None:
        return gdf.to_crs(target_crs)
    if gdf.crs is not None and gdf.crs.is_projected:
        return gdf  # already metric — no reprojection needed
    # Geographic CRS (e.g. EPSG:4326) → estimate UTM zone
    reference = gdf if not gdf.empty else (fallback_gdf or gdf)
    utm = reference.estimate_utm_crs()
    return gdf.to_crs(utm)


def filter_gdf_to_boundary(
    gdf: gpd.GeoDataFrame, boundary: dict
) -> gpd.GeoDataFrame:
    """Return rows of *gdf* whose geometry intersects *boundary*.

    Parameters
    ----------
    gdf:
        GeoDataFrame to filter.  Rows with null geometry are excluded.
    boundary:
        A GeoJSON Polygon/MultiPolygon **geometry** dict, or a **Feature** dict
        wrapping such a geometry.  The coordinates must be in the same CRS as
        *gdf*.

    Returns
    -------
    gpd.GeoDataFrame
        Subset of *gdf* whose geometries intersect the boundary.
    """
    from shapely.geometry import shape

    geom_dict = (
        boundary["geometry"]
        if boundary.get("type") == "Feature"
        else boundary
    )
    boundary_geom = shape(geom_dict)
    mask = gdf.geometry.notna() & gdf.geometry.intersects(boundary_geom)
    return gdf[mask]


def filter_features_to_boundary(
    features: list[dict], boundary: dict
) -> list[dict]:
    """Return only features whose geometry intersects *boundary*.

    Parameters
    ----------
    features:
        GeoJSON feature dicts (any geometry type).
    boundary:
        A GeoJSON Polygon/MultiPolygon **geometry** dict, or a **Feature** dict
        wrapping such a geometry.  The coordinates must be in the same CRS as
        *features* (typically EPSG:4326 for raw GeoJSON inputs).

    Returns
    -------
    list[dict]
        Subset of *features* whose geometries intersect the boundary.
        Features with null/missing geometries are excluded.
    """
    from shapely.geometry import shape

    geom_dict = (
        boundary["geometry"]
        if boundary.get("type") == "Feature"
        else boundary
    )
    boundary_geom = shape(geom_dict)

    return [
        f for f in features
        if (g := f.get("geometry")) and shape(g).intersects(boundary_geom)
    ]


def expand_boundary_by_meters(boundary: dict, meters: float) -> dict:
    """Return a new GeoJSON geometry dict expanded outward by *meters*.

    Reprojects to an auto-detected metric UTM CRS, applies a Minkowski buffer,
    then reprojects back to WGS-84 (EPSG:4326).

    Parameters
    ----------
    boundary:
        A GeoJSON Polygon/MultiPolygon **geometry** dict, or a **Feature** dict
        wrapping such a geometry.  Coordinates must be in WGS-84.
    meters:
        Buffer distance in metres.

    Returns
    -------
    dict
        Expanded GeoJSON geometry dict (same type as input, coordinates in WGS-84).
    """

    geom_dict = (
        boundary["geometry"]
        if boundary.get("type") == "Feature"
        else boundary
    )
    geom = shape(geom_dict)
    gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    gdf_metric = gdf.to_crs(gdf.estimate_utm_crs())
    gdf_metric = gdf_metric.copy()
    gdf_metric["geometry"] = gdf_metric.geometry.buffer(meters)
    expanded = gdf_metric.to_crs("EPSG:4326").geometry.iloc[0]
    return dict(mapping(expanded))

