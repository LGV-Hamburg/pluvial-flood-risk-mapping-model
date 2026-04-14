"""Integration tests for the PluvialFloodRiskProcess pipeline branches.

What is an integration test here?
----------------------------------
The unit tests in ``test_pluvial_flood_risk.py`` call individual algorithm
functions (``compute_sensitivity``, ``run_exposure_pipeline``, …) directly
with plain DataFrames.  They prove that the *math* is correct.

The tests here exercise the full data path that a real OGC API call follows:

    GeoJSON FeatureCollection (exec_body)
        ↓
    PluvialFloodRiskProcess.vulnerability_pipeline / exposure_pipeline
        ↓
    Input parsing (dict → pd.DataFrame / gpd.GeoDataFrame)
        ↓
    Spatial pre-processing (coordinate projection, area computation)
        ↓
    Algorithm modules (topsis, vulnerability, exposure)
        ↓
    Serialisation-ready output dict  (column → {building_id → value})

This level of test catches wiring bugs that unit tests cannot, such as:
- Field-name mismatches between the process layer and algorithm modules
- Geometry handling (the exposure branch derives footprint area from the
  GeoJSON polygon via a UTM projection — this step is absent in unit tests)
- Index alignment between the GeoDataFrame and the result DataFrame
- Output structure expected by the (future) merge_results step

Infrastructure requirement
--------------------------
Instantiating ``PluvialFloodRiskProcess`` triggers the fastprocesses library
to verify its Redis/Celery connection.  These tests therefore require a
running Redis instance.  In the dev environment start it with:

    docker compose -f docker-compose-dev.yaml up -d redis

Tests are marked ``@pytest.mark.integration`` so they can be run selectively:

    python -m pytest -m integration           # integration tests only
    python -m pytest -m "not integration"     # unit tests only

Currently implemented (and therefore tested) branches
------------------------------------------------------
- vulnerability_pipeline  (Steps 1.1–1.4)
- exposure_pipeline       (Step 2)

The hazard and merge branches are not yet implemented and are excluded.
"""

from __future__ import annotations

import math

import pytest

from pluvial_flood_risk.models.field_names import (
    BUILDING_LIVING_AREA_M2,
    COPING_CAPACITY_INDEX,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_PER_BUILDING,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
)

# ---------------------------------------------------------------------------
# Import triggers @register_process side-effect — intentional.
# ---------------------------------------------------------------------------
from pluvial_flood_risk.processes.flood_risk_process import PluvialFloodRiskProcess

# ---------------------------------------------------------------------------
# Helpers — compact GeoJSON polygon generator (Hamburg area)
# ---------------------------------------------------------------------------

# Anchor point: near Hamburg Rathaus (9.99 °E, 53.55 °N)
_LON0, _LAT0 = 9.9927, 53.5503
# Rectangle size: ≈ 0.0002° lon × 0.0002° lat ≈ 13 m × 22 m ≈ 290 m²
_DL, _DB = 0.0002, 0.0002


def _rect(lon: float, lat: float) -> dict:
    """Return a closed GeoJSON Polygon at the given lower-left corner."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + _DL, lat],
                [lon + _DL, lat + _DB],
                [lon, lat + _DB],
                [lon, lat],
            ]
        ],
    }


# ---------------------------------------------------------------------------
# Shared exec_body fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exec_body() -> dict:
    """Minimal but realistic OGC exec_body with three Hamburg buildings.

    Building layout (all in statistical unit 100):

    id=1  type=1, 1 floor   → single-storey house; R_G = R / 1 = R
    id=2  type=1, 4 floors  → apartment block;     R_G = R / 4
    id=3  type=2, 3 floors  → commercial GF;       R_G = 0

    The vulnerability indicators are chosen so the three buildings produce
    clearly different SVI scores (descending: B1 > B2 > B3).
    """

    def _feature(fid, lon, floors, btype, c, es, wr, edq):
        return {
            "type": "Feature",
            "id": fid,
            "geometry": _rect(lon, _LAT0),
            "properties": {
                "id": fid,
                "floors_above_ground": floors,
                "building_type": btype,
                # Sensitivity indicators
                "children_under_10": c,
                "elderly_singles_over_65": es,
                # Coping-capacity indicators
                "social_welfare_recipients": wr,
                "no_highschool_diploma": edq,
                # Statistical-unit population lookup
                "statistical_unit_id": 100,
                "residents_in_statistical_unit": 500.0,
                "living_area_m2_statistical_unit": 18_000.0,
            },
        }

    return {
        "inputs": {
            "buildings": {
                "type": "FeatureCollection",
                "features": [
                    # High vulnerability: many children and elderly, poor coping
                    _feature(1, _LON0, 1, 1, c=20, es=15, wr=18, edq=30),
                    # Medium vulnerability
                    _feature(2, _LON0 + 0.0004, 4, 1, c=8, es=5, wr=6, edq=12),
                    # Low vulnerability: commercial ground floor, good coping
                    _feature(3, _LON0 + 0.0008, 3, 2, c=1, es=1, wr=1, edq=2),
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Shared process fixture — one instance per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def process() -> PluvialFloodRiskProcess:
    """Single PluvialFloodRiskProcess instance shared across the module."""
    return PluvialFloodRiskProcess()


# ---------------------------------------------------------------------------
# 1. Process registration
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProcessRegistration:
    """The @register_process decorator fires at import time, not instantiation."""

    def test_process_id_in_registry(self) -> None:
        from fastprocesses.processes.process_registry import get_process_registry

        registry = get_process_registry()
        assert "pluvial_flood_risk" in registry.get_process_ids()

    def test_process_description_accessible(
        self, process: PluvialFloodRiskProcess
    ) -> None:
        desc = process.process_description
        assert desc.id == "pluvial_flood_risk"
        assert desc.version == "1.0.0"
        assert "buildings" in desc.inputs
        assert "risk_features" in desc.outputs


# ---------------------------------------------------------------------------
# 2. vulnerability_pipeline integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVulnerabilityPipelineIntegration:
    """vulnerability_pipeline: GeoJSON exec_body → vulnerability output dict."""

    @pytest.fixture(scope="class")
    def result(self, process: PluvialFloodRiskProcess, exec_body: dict) -> dict:
        return process.vulnerability_pipeline(exec_body)

    def test_returns_four_output_keys(self, result: dict) -> None:
        expected = {
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        }
        assert set(result.keys()) == expected

    def test_all_building_ids_present(self, result: dict) -> None:
        for col_dict in result.values():
            assert set(col_dict.keys()) == {1, 2, 3}

    def test_sensitivity_values_non_negative(self, result: dict) -> None:
        # With apply_entropy=True the multiplier U ∈ [1, 2], so the
        # entropy-weighted TOPSIS score lies in [0, 2], not [0, 1].
        for bid, val in result[SENSITIVITY_INDEX].items():
            assert val is not None, f"None for building {bid}"
            assert 0.0 <= val <= 2.0, f"Out of range for building {bid}: {val}"

    def test_coping_capacity_values_non_negative(self, result: dict) -> None:
        for bid, val in result[COPING_CAPACITY_INDEX].items():
            assert val is not None
            assert 0.0 <= val <= 2.0

    def test_svi_values_non_negative(self, result: dict) -> None:
        for bid, val in result[SOCIAL_VULNERABILITY_INDEX].items():
            assert val is not None
            assert 0.0 <= val <= 2.0

    def test_sv_pf_strictly_positive(self, result: dict) -> None:
        for bid, val in result[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL].items():
            assert val is not None
            assert val > 0.0, f"SV_PF ≤ 0 for building {bid}"

    def test_high_vulnerability_building_has_highest_svi(self, result: dict) -> None:
        """Building 1 has the highest indicator values → highest SVI."""
        svi = result[SOCIAL_VULNERABILITY_INDEX]
        assert svi[1] >= svi[2], "B1 SVI should be ≥ B2 SVI"
        assert svi[2] >= svi[3], "B2 SVI should be ≥ B3 SVI"

    def test_no_nan_in_output(self, result: dict) -> None:
        for col, col_dict in result.items():
            for bid, val in col_dict.items():
                assert val is None or not math.isnan(val), (
                    f"Unexpected NaN for {col}[{bid}]"
                )


# ---------------------------------------------------------------------------
# 3. exposure_pipeline integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExposurePipelineIntegration:
    """exposure_pipeline: GeoJSON exec_body (with geometry) → exposure dict.

    This branch does more than vulnerability_pipeline: it parses GeoJSON
    Polygon geometries into a GeoDataFrame, re-projects to UTM, and derives
    the building footprint area from the polygon geometry.  That spatial
    preprocessing step is *unique* to the process layer and is not covered
    by unit tests.
    """

    @pytest.fixture(scope="class")
    def result(self, process: PluvialFloodRiskProcess, exec_body: dict) -> dict:
        return process.exposure_pipeline(exec_body)

    def test_returns_four_output_keys(self, result: dict) -> None:
        expected = {
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        }
        assert set(result.keys()) == expected

    def test_all_building_ids_present(self, result: dict) -> None:
        for col_dict in result.values():
            assert set(col_dict.keys()) == {1, 2, 3}

    def test_footprint_area_is_positive_for_all_buildings(self, result: dict) -> None:
        """The UTM-projected polygon area must be strictly positive."""
        for bid, val in result[BUILDING_LIVING_AREA_M2].items():
            assert val is not None and val > 0.0, (
                f"Living area ≤ 0 for building {bid}: {val}"
            )

    def test_residents_per_building_positive(self, result: dict) -> None:
        for bid, val in result[RESIDENTS_PER_BUILDING].items():
            assert val is not None and val > 0.0, f"R ≤ 0 for building {bid}: {val}"

    def test_all_residents_sum_le_statistical_unit_total(self, result: dict) -> None:
        """The three buildings share one stat unit (500 residents).
        Their combined R must not exceed that total."""
        total_r = sum(result[RESIDENTS_PER_BUILDING].values())
        assert total_r <= 500.0 + 1e-6

    def test_ground_floor_zero_for_commercial_building(self, result: dict) -> None:
        """Building 3 is type 2 (commercial GF) → R_G must be 0."""
        r_g_b3 = result[RESIDENTS_GROUND_FLOOR][3]
        assert r_g_b3 == pytest.approx(0.0, abs=1e-9)

    def test_single_floor_building_all_residents_on_ground_floor(
        self, result: dict
    ) -> None:
        """Building 1 has 1 floor → R_G = R / 1 = R."""
        r_b1 = result[RESIDENTS_PER_BUILDING][1]
        r_g_b1 = result[RESIDENTS_GROUND_FLOOR][1]
        assert r_g_b1 == pytest.approx(r_b1, rel=1e-9)

    def test_ground_floor_le_total_residents(self, result: dict) -> None:
        """Invariant: R_G ≤ R for every building."""
        for bid in (1, 2, 3):
            r = result[RESIDENTS_PER_BUILDING][bid]
            r_g = result[RESIDENTS_GROUND_FLOOR][bid]
            assert r_g <= r + 1e-9, f"R_G ({r_g}) > R ({r}) for building {bid}"

    def test_ema_equals_residents_per_building(self, result: dict) -> None:
        """EMA is identical to R — all residents are at risk of mobility loss."""
        for bid in (1, 2, 3):
            assert result[EXPOSURE_MOBILITY_ACCESSIBILITY][bid] == pytest.approx(
                result[RESIDENTS_PER_BUILDING][bid], rel=1e-9
            )

    def test_apartment_block_has_higher_living_area_than_single_house(
        self, result: dict
    ) -> None:
        """Building 2 (4 floors, type 1) has a larger footprint × 4 floors.
        Building 1 is 1 floor, both have similar footprint sizes.
        Area_house[2] should be about 4× Area_house[1]."""
        area_b1 = result[BUILDING_LIVING_AREA_M2][1]
        area_b2 = result[BUILDING_LIVING_AREA_M2][2]
        assert area_b2 == pytest.approx(4 * area_b1, rel=0.05)


# ---------------------------------------------------------------------------
# 4. Cross-pipeline consistency
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCrossPipelineConsistency:
    """Both implemented branches must return consistent building ids and
    non-conflicting values for the same exec_body."""

    @pytest.fixture(scope="class")
    def vuln_result(self, process: PluvialFloodRiskProcess, exec_body: dict) -> dict:
        return process.vulnerability_pipeline(exec_body)

    @pytest.fixture(scope="class")
    def expo_result(self, process: PluvialFloodRiskProcess, exec_body: dict) -> dict:
        return process.exposure_pipeline(exec_body)

    def test_same_building_ids_in_both_branches(
        self, vuln_result: dict, expo_result: dict
    ) -> None:
        vuln_ids = set(next(iter(vuln_result.values())).keys())
        expo_ids = set(next(iter(expo_result.values())).keys())
        assert vuln_ids == expo_ids == {1, 2, 3}

    def test_no_missing_values_across_branches(
        self, vuln_result: dict, expo_result: dict
    ) -> None:
        for col_dict in (*vuln_result.values(), *expo_result.values()):
            for bid, val in col_dict.items():
                assert val is not None, f"None value for building {bid}"


# ---------------------------------------------------------------------------
# 5. Not-yet-implemented branches raise NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUnimplementedBranches:
    """Unimplemented branches must raise NotImplementedError so failures are
    explicit rather than silent (e.g. a no-op that returns empty results)."""

    def test_merge_results_not_implemented(
        self, process: PluvialFloodRiskProcess
    ) -> None:
        with pytest.raises(NotImplementedError):
            process.merge_results({})


# ---------------------------------------------------------------------------
# 6. exposure_pipeline — statistical_units join mode
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exec_body_with_stat_units() -> dict:
    """Same three buildings as exec_body, but residents/living_area are NOT
    pre-joined into the building properties.  Instead a separate
    'statistical_units' FeatureCollection is provided and the join is done
    inside exposure_pipeline.

    Both modes must produce identical numeric results.
    """

    def _feature(fid, lon, floors, btype, c, es, wr, edq):
        return {
            "type": "Feature",
            "id": fid,
            "geometry": _rect(lon, _LAT0),
            "properties": {
                "id": fid,
                "floors_above_ground": floors,
                "building_type": btype,
                "children_under_10": c,
                "elderly_singles_over_65": es,
                "social_welfare_recipients": wr,
                "no_highschool_diploma": edq,
                # Only the FK — no residents/living_area here
                "statistical_unit_id": 100,
            },
        }

    return {
        "inputs": {
            "buildings": {
                "type": "FeatureCollection",
                "features": [
                    _feature(1, _LON0, 1, 1, c=20, es=15, wr=18, edq=30),
                    _feature(2, _LON0 + 0.0004, 4, 1, c=8, es=5, wr=6, edq=12),
                    _feature(3, _LON0 + 0.0008, 3, 2, c=1, es=1, wr=1, edq=2),
                ],
            },
            # Lookup table supplied as a separate input
            "statistical_units": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": {
                            "statistical_unit_id": 100,
                            "residents": 500.0,
                            "living_area_m2": 18_000.0,
                        },
                    }
                ],
            },
        }
    }


@pytest.mark.integration
class TestExposurePipelineStatisticalUnitsJoin:
    """exposure_pipeline — run with statistical_units as separate input.

    Verifies that the automatic join path produces the same results as when
    resident/living-area values are pre-joined into the buildings properties.
    """

    @pytest.fixture(scope="class")
    def result_joined(self, process: PluvialFloodRiskProcess, exec_body: dict) -> dict:
        """Result from pre-joined mode (baseline)."""
        return process.exposure_pipeline(exec_body)

    @pytest.fixture(scope="class")
    def result_lookup(
        self,
        process: PluvialFloodRiskProcess,
        exec_body_with_stat_units: dict,
    ) -> dict:
        """Result from statistical_units join mode."""
        return process.exposure_pipeline(exec_body_with_stat_units)

    def test_lookup_mode_returns_four_output_keys(self, result_lookup: dict) -> None:
        expected = {
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        }
        assert set(result_lookup.keys()) == expected

    def test_lookup_mode_all_building_ids_present(self, result_lookup: dict) -> None:
        for col_dict in result_lookup.values():
            assert set(col_dict.keys()) == {1, 2, 3}

    def test_lookup_mode_matches_prejoined_mode(
        self, result_joined: dict, result_lookup: dict
    ) -> None:
        """Both input modes must yield numerically identical results."""
        for col in result_joined:
            for bid in (1, 2, 3):
                assert result_lookup[col][bid] == pytest.approx(
                    result_joined[col][bid], rel=1e-9
                ), f"Mismatch in {col}[{bid}]"

    def test_missing_both_inputs_raises_value_error(
        self, process: PluvialFloodRiskProcess
    ) -> None:
        """If neither mode is provided the process must fail with a clear message."""
        bad_body = {
            "inputs": {
                "buildings": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": 1,
                            "geometry": _rect(_LON0, _LAT0),
                            "properties": {
                                "id": 1,
                                "floors_above_ground": 2,
                                "building_type": 1,
                                "statistical_unit_id": 100,
                                # residents_in_statistical_unit and
                                # living_area_m2_statistical_unit intentionally absent
                            },
                        }
                    ],
                }
                # statistical_units intentionally absent
            }
        }
        with pytest.raises(ValueError, match="statistical_units"):
            process.exposure_pipeline(bad_body)


# ---------------------------------------------------------------------------
# 7. exposure_pipeline — spatial (centroid-within) join mode
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exec_body_spatial_join() -> dict:
    """Same three buildings, but WITHOUT statistical_unit_id on the features.

    The statistical unit polygon is a large bounding box that contains all
    three building centroids.  The join must be performed spatially.
    """
    # Bounding box covering all three buildings with generous margin:
    # lon ∈ [_LON0 - 0.001, _LON0 + 0.002], lat ∈ [_LAT0 - 0.001, _LAT0 + 0.002]
    unit_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [_LON0 - 0.001, _LAT0 - 0.001],
                [_LON0 + 0.002, _LAT0 - 0.001],
                [_LON0 + 0.002, _LAT0 + 0.002],
                [_LON0 - 0.001, _LAT0 + 0.002],
                [_LON0 - 0.001, _LAT0 - 0.001],
            ]
        ],
    }

    def _feature(fid, lon, floors, btype, c, es, wr, edq):
        return {
            "type": "Feature",
            "id": fid,
            "geometry": _rect(lon, _LAT0),
            "properties": {
                "id": fid,
                "floors_above_ground": floors,
                "building_type": btype,
                "children_under_10": c,
                "elderly_singles_over_65": es,
                "social_welfare_recipients": wr,
                "no_highschool_diploma": edq,
                # No statistical_unit_id → forces spatial join
            },
        }

    return {
        "inputs": {
            "buildings": {
                "type": "FeatureCollection",
                "features": [
                    _feature(1, _LON0, 1, 1, c=20, es=15, wr=18, edq=30),
                    _feature(2, _LON0 + 0.0004, 4, 1, c=8, es=5, wr=6, edq=12),
                    _feature(3, _LON0 + 0.0008, 3, 2, c=1, es=1, wr=1, edq=2),
                ],
            },
            "statistical_units": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": unit_poly,
                        "properties": {
                            "residents": 500.0,
                            "living_area_m2": 18_000.0,
                        },
                    }
                ],
            },
        }
    }


@pytest.mark.integration
class TestExposurePipelineSpatialJoin:
    """exposure_pipeline — spatial centroid-within join (no statistical_unit_id)."""

    @pytest.fixture(scope="class")
    def result_spatial(
        self,
        process: PluvialFloodRiskProcess,
        exec_body_spatial_join: dict,
    ) -> dict:
        return process.exposure_pipeline(exec_body_spatial_join)

    @pytest.fixture(scope="class")
    def result_attr(
        self, process: PluvialFloodRiskProcess, exec_body_with_stat_units: dict
    ) -> dict:
        """Attribute-join result as baseline (same residents/living_area values)."""
        return process.exposure_pipeline(exec_body_with_stat_units)

    def test_spatial_join_returns_four_output_keys(
        self, result_spatial: dict
    ) -> None:
        expected = {
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        }
        assert set(result_spatial.keys()) == expected

    def test_spatial_join_all_building_ids_present(
        self, result_spatial: dict
    ) -> None:
        for col_dict in result_spatial.values():
            assert set(col_dict.keys()) == {1, 2, 3}

    def test_spatial_join_matches_attribute_join(
        self, result_spatial: dict, result_attr: dict
    ) -> None:
        """Spatial join must yield the same numeric results as attribute join."""
        for col in result_attr:
            for bid in (1, 2, 3):
                assert result_spatial[col][bid] == pytest.approx(
                    result_attr[col][bid], rel=1e-9
                ), f"Mismatch in {col}[{bid}]"
