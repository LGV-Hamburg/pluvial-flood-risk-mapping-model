import geopandas as gpd
from shapely.geometry import box, mapping

from pluvial_flood_risk.spatial_utils import (
    filter_features_to_boundary,
    filter_features_to_intersecting_units,
)


def test_projected_buildings_intersect_wgs84_boundary_units():
    boundary = box(10.05, 53.56, 10.08, 53.58)
    unit = box(10.04, 53.55, 10.09, 53.59)
    building = box(10.06, 53.57, 10.061, 53.571)
    projected = gpd.GeoSeries([building], crs="EPSG:4326").to_crs("EPSG:25832")[0]
    features = [
        {"type": "Feature", "geometry": mapping(projected), "properties": {"id": 1}}
    ]
    units = [{"type": "Feature", "geometry": mapping(unit), "properties": {}}]

    selected = filter_features_to_intersecting_units(
        features, units, mapping(boundary), mapping(boundary.buffer(0.01)),
        features_crs="EPSG:25832",
    )

    assert selected == features


def test_projected_units_and_buildings_intersect_wgs84_boundary():
    boundary = box(10.05, 53.56, 10.08, 53.58)
    unit = box(10.04, 53.55, 10.09, 53.59)
    building = box(10.0405, 53.5505, 10.041, 53.551)
    projected = gpd.GeoSeries([unit, building], crs="EPSG:4326").to_crs("EPSG:25832")
    features = [
        {"type": "Feature", "geometry": mapping(projected[1]), "properties": {"id": 1}}
    ]
    units = [{"type": "Feature", "geometry": mapping(projected[0]), "properties": {}}]

    assert filter_features_to_intersecting_units(
        features, units, mapping(boundary), mapping(boundary.buffer(0.0003)),
        features_crs="EPSG:25832",
    ) == features


def test_projected_buildings_survive_exact_wgs84_boundary_filter():
    boundary = box(10.05, 53.56, 10.08, 53.58)
    building = box(10.06, 53.57, 10.061, 53.571)
    projected = gpd.GeoSeries([building], crs="EPSG:4326").to_crs("EPSG:25832")[0]
    features = [
        {"type": "Feature", "geometry": mapping(projected), "properties": {"id": 1}}
    ]

    assert filter_features_to_boundary(
        features, mapping(boundary), features_crs="EPSG:25832"
    ) == features


def test_wgs84_features_survive_when_target_crs_is_projected():
    boundary = box(10.05, 53.56, 10.08, 53.58)
    building = box(10.06, 53.57, 10.061, 53.571)
    features = [
        {"type": "Feature", "geometry": mapping(building), "properties": {"id": 1}}
    ]

    assert filter_features_to_boundary(
        features, mapping(boundary), features_crs="EPSG:25832"
    ) == features