"""Spatial utility helpers shared across pipeline branches."""

from __future__ import annotations

import logging
from typing import cast

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

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
        CRS to assign when no CRS is embedded in the features.  Supply a known
        source CRS only for data already in a projected system (e.g.
        ``'EPSG:25832'``).  Defaults to ``'EPSG:4326'`` for WGS-84 GeoJSON.
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
    # Ensure the "id" column uses the canonical feature ID — the same priority
    # order that vulnerability_pipeline applies: top-level feature["id"] first,
    # then feature["properties"]["id"].  from_features() only reads properties,
    # so when features carry a top-level id (e.g. DEHHALKA strings in Hamburg
    # city-wide data) that differs from properties.id (integer), the column
    # would be wrong.  Overwriting it here makes all pipelines consistent.
    _canonical_ids = [
        f.get("id") or (f.get("properties") or {}).get("id")
        for f in features
    ]
    if any(x is not None for x in _canonical_ids):
        gdf["id"] = _canonical_ids
    # from_features([]) returns a plain DataFrame with no geometry column;
    # also guard against datasets where all geometries are absent.
    if "geometry" not in gdf.columns:
        return gpd.GeoDataFrame(
            gdf,
            geometry=gpd.GeoSeries(dtype="geometry"),
            crs=source_crs or "EPSG:4326",
        )
    if gdf.crs is None:
        _effective_crs = source_crs or "EPSG:4326"
        # Guard: if the caller declared a projected CRS but the actual
        # coordinates look geographic (|x| ≤ 180, |y| ≤ 90), the data is
        # WGS84 GeoJSON and the projected label is wrong.  This happens when
        # target_crs is passed as source_crs for remotely-fetched data that
        # the OGC API returned in EPSG:4326.  Fall back to EPSG:4326 so that
        # project_crs_to_metric can reproject correctly.
        if source_crs and not gdf.empty and "geometry" in gdf.columns:
            try:
                from pyproj import CRS as _ProjCRS
                if _ProjCRS.from_user_input(source_crs).is_projected:
                    _valid_geoms = gdf["geometry"].dropna()
                    if not _valid_geoms.empty:
                        _bounds = gpd.GeoSeries(_valid_geoms).total_bounds
                        if (
                            _bounds.size == 4
                            and abs(_bounds[0]) <= 181
                            and abs(_bounds[2]) <= 181
                            and abs(_bounds[1]) <= 91
                            and abs(_bounds[3]) <= 91
                        ):
                            logger.warning(
                                "convert_geojson_to_gdf: declared CRS %r is "
                                "projected but coordinates look geographic "
                                "(bounds %s) — assuming EPSG:4326; data will be "
                                "reprojected to target CRS by project_crs_to_metric",
                                source_crs,
                                tuple(round(float(v), 4) for v in _bounds),
                            )
                            _effective_crs = "EPSG:4326"
            except Exception:
                pass
        gdf = gdf.set_crs(_effective_crs)
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
        wrapping such a geometry.  Assumed to be in EPSG:4326 (WGS-84);
        automatically reprojected to match *gdf*'s CRS when they differ.

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
    # Reproject WGS-84 boundary to the GDF's CRS when they differ so that
    # the intersection test is CRS-consistent (e.g. after project_crs_to_metric).
    if gdf.crs is not None:
        try:
            _bdf = gpd.GeoDataFrame(geometry=[boundary_geom], crs="EPSG:4326")
            if _bdf.crs is not None and not _bdf.crs.equals(gdf.crs):
                boundary_geom = _bdf.to_crs(gdf.crs).geometry.iloc[0]  # type: ignore[assignment]
        except Exception:
            pass
    mask = gdf.geometry.notna() & gdf.geometry.intersects(boundary_geom)  # type: ignore[arg-type]
    return gdf[mask]


def filter_features_to_boundary(
    features: list[dict],
    boundary: dict,
    features_crs: str | None = None,
) -> list[dict]:
    """Return only features whose geometry intersects *boundary*.

    Parameters
    ----------
    features:
        GeoJSON feature dicts (any geometry type).
    boundary:
        A GeoJSON Polygon/MultiPolygon **geometry** dict, or a **Feature** dict
        wrapping such a geometry.  Assumed to be in EPSG:4326 (WGS-84).
    features_crs:
        Known source CRS of *features* as an EPSG token or OGC URI.  When
        different from WGS-84, the boundary is reprojected to this CRS.

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
    first_geometry = next((f for f in features if f.get("geometry")), None)
    if first_geometry is not None:
        actual_crs = convert_geojson_to_gdf([first_geometry], features_crs).crs
        if actual_crs is not None and not actual_crs.equals("EPSG:4326"):
            boundary_geom = gpd.GeoSeries(
                [boundary_geom], crs="EPSG:4326"
            ).to_crs(actual_crs).iloc[0]

    return [
        f for f in features
        if (g := f.get("geometry")) and shape(g).intersects(boundary_geom)  # type: ignore[arg-type]
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
    return dict(mapping(expanded))  # type: ignore[arg-type]


def filter_features_to_intersecting_units(
    features: list[dict], units: list[dict], boundary: dict, halo: dict,
    features_crs: str | None = None,
) -> list[dict]:
    """Return features intersecting the union of *units* that intersect *boundary*.

    Used to pre-shrink the building set passed to local pipeline branches:
    keep every building within any statistical unit that the calculation
    boundary touches, so unit-level aggregations (e.g. living-area sums) stay
    correct, while dropping the rest of the city.  Falls back to the halo
    region when no usable unit geometries are available.

    Parameters
    ----------
    features:
        Building feature dicts.
    units:
        Statistical unit feature dicts; may be empty.
    boundary:
        Calculation boundary geometry/Feature dict.
    halo:
        Boundary already expanded by the hazard halo; used as fallback region
        when no statistical units are supplied.
    features_crs:
        Declared CRS of building coordinates. Geographic-looking coordinates
        are treated as WGS-84, as in ``convert_geojson_to_gdf``.

    Returns
    -------
    list[dict]
        Buildings intersecting the intersecting-units union (or the halo).
    """
    def _geom(b: dict):
        return shape(b["geometry"] if b.get("type") == "Feature" else b)

    boundary_geom = _geom(boundary)
    first_unit = next((unit for unit in units if unit.get("geometry")), None)
    unit_crs = (
        convert_geojson_to_gdf([first_unit], features_crs).crs
        if first_unit is not None else None
    )
    if unit_crs is not None and not unit_crs.equals("EPSG:4326"):
        boundary_geom = cast(
            BaseGeometry,
            gpd.GeoSeries([boundary_geom], crs="EPSG:4326").to_crs(unit_crs).iloc[0],
        )
    unit_geoms = [
        shape(g)
        for u in units
        if (g := u.get("geometry")) and shape(g).intersects(boundary_geom)
    ]
    region = unary_union(unit_geoms) if unit_geoms else _geom(halo)
    region_crs = unit_crs if unit_geoms else "EPSG:4326"
    first_geometry = next((f for f in features if f.get("geometry")), None)
    if first_geometry is not None:
        building_crs = convert_geojson_to_gdf([first_geometry], features_crs).crs
        if building_crs is not None and not building_crs.equals(region_crs):
            region = cast(
                BaseGeometry,
                gpd.GeoSeries([region], crs=region_crs).to_crs(building_crs).iloc[0],
            )
    return [
        f for f in features
        if (g := f.get("geometry")) and shape(g).intersects(region)
    ]

