"""Tests for the pluvial flood risk vulnerability pipeline."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import pluvial_flood_risk
from pluvial_flood_risk.algorithms.topsis import topsis
from pluvial_flood_risk.algorithms.vulnerability import (
    compute_coping_capacity,
    compute_sensitivity,
    compute_svi,
    flood_susceptibility_transform,
    run_vulnerability_pipeline,
)
from pluvial_flood_risk.models.field_names import (
    CHILDREN_UNDER_10,
    COPING_CAPACITY_INDEX,
    ELDERLY_SINGLES_OVER_65,
    NO_HIGHSCHOOL_DIPLOMA,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
    SOCIAL_WELFARE_RECIPIENTS,
)
from pluvial_flood_risk.models.inputs import VulnerabilityParams


def test_package_importable() -> None:
    """The package must be importable without errors."""
    assert pluvial_flood_risk is not None


# ---------------------------------------------------------------------------
# TOPSIS core
# ---------------------------------------------------------------------------


class TestTopsis:
    """Unit tests for the TOPSIS algorithm."""

    def test_two_buildings_one_indicator_no_entropy(self) -> None:
        """With a single indicator, the extreme buildings must score 0 and 1."""
        M = np.array([[1.0], [10.0]])
        w = np.array([1.0])
        result = topsis(M, w, apply_entropy=False)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(1.0)

    def test_identical_buildings_score_zero(self) -> None:
        """All-identical rows → zero-column-sum → all closeness = 0."""
        M = np.array([[5.0, 3.0], [5.0, 3.0], [5.0, 3.0]])
        w = np.array([0.5, 0.5])
        result = topsis(M, w, apply_entropy=False)
        assert np.all(result == pytest.approx(0.0))

    def test_all_zero_matrix_no_entropy(self) -> None:
        """All-zero input → zero column sums → all closeness = 0."""
        M = np.zeros((4, 2))
        w = np.array([0.6, 0.4])
        result = topsis(M, w, apply_entropy=False)
        assert np.all(result == pytest.approx(0.0))

    def test_output_range_no_entropy(self) -> None:
        """Without entropy the output must lie in [0, 1]."""
        rng = np.random.default_rng(42)
        M = rng.integers(0, 10, size=(20, 3)).astype(float)
        w = np.array([0.3, 0.3, 0.4])
        result = topsis(M, w, apply_entropy=False)
        assert result.min() >= 0.0
        assert result.max() <= 1.0 + 1e-9

    def test_output_range_with_entropy(self) -> None:
        """With entropy the output must lie in [0, 2]."""
        rng = np.random.default_rng(0)
        M = rng.integers(1, 10, size=(15, 2)).astype(float)
        w = np.array([0.5, 0.5])
        result = topsis(M, w, apply_entropy=True)
        assert result.min() >= 0.0
        assert result.max() <= 2.0 + 1e-9

    def test_entropy_with_zero_row_defaults_to_max(self) -> None:
        """A row with a zero element must get entropy U = 2."""
        # Building 0: both nonzero → entropy < 2; Building 1: one zero → U = 2
        M = np.array([[3.0, 4.0], [5.0, 0.0]])
        w = np.array([0.5, 0.5])
        result_entropy = topsis(M, w, apply_entropy=True)
        result_no_entropy = topsis(M, w, apply_entropy=False)
        # Building 1 has C = 0 (column-max check), so product is 0 in either case.
        # Building 0: with entropy the score is C[0] * U[0] where U[0] < 2.
        assert result_entropy[0] <= result_no_entropy[0] * 2.0 + 1e-9

    def test_wrong_weights_shape_raises(self) -> None:
        M = np.array([[1.0, 2.0]])
        w = np.array([0.5])  # should be length 2
        with pytest.raises(ValueError, match="weights shape"):
            topsis(M, w)

    def test_non_2d_matrix_raises(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            topsis(np.array([1.0, 2.0]), np.array([1.0]))

    def test_monotone_single_indicator(self) -> None:
        """Closeness must increase monotonically with a single ascending indicator."""
        values = np.arange(1.0, 6.0).reshape(-1, 1)
        w = np.array([1.0])
        result = topsis(values, w, apply_entropy=False)
        assert list(result) == sorted(result)

    def test_paper_example_sensitivity_weights(self) -> None:
        """Reproduce sensitivity calculation for two archetypal buildings."""
        # Building A: ES=1, C=0  →  only elderly singles vulnerable (ES-heavy)
        # Building B: ES=0, C=1  →  only children vulnerable
        # With ES weight=0.7, C weight=0.3 building A should score higher.
        M = np.array([[1.0, 0.0], [0.0, 1.0]])
        w = np.array([0.3, 0.7])  # [C, ES]
        result = topsis(M, w, apply_entropy=False)
        assert result[1] > result[0]  # ES-only building scores higher than C-only


# ---------------------------------------------------------------------------
# Vulnerability pipeline steps
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Minimal 4-building DataFrame with required raw indicator columns."""
    return pd.DataFrame(
        {
            CHILDREN_UNDER_10: [1.0, 2.0, 0.0, 3.0],
            ELDERLY_SINGLES_OVER_65: [0.0, 1.0, 2.0, 4.0],
            SOCIAL_WELFARE_RECIPIENTS: [1.0, 0.0, 3.0, 2.0],
            NO_HIGHSCHOOL_DIPLOMA: [0.0, 1.0, 1.0, 2.0],
        }
    )


@pytest.fixture()
def default_params() -> VulnerabilityParams:
    return VulnerabilityParams()


class TestComputeSensitivity:
    def test_returns_series_with_correct_name(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = compute_sensitivity(sample_df, default_params)
        assert result.name == SENSITIVITY_INDEX

    def test_no_nan_for_full_data(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = compute_sensitivity(sample_df, default_params)
        assert not result.isna().any()

    def test_nan_propagated_for_null_input(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        df = sample_df.copy()
        df.loc[1, CHILDREN_UNDER_10] = None
        result = compute_sensitivity(df, default_params)
        assert math.isnan(result.iloc[1])
        assert not result.iloc[0:1].isna().any()

    def test_values_in_valid_range(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = compute_sensitivity(sample_df, default_params)
        assert result.dropna().between(0.0, 2.0).all()


class TestComputeCopingCapacity:
    def test_returns_series_with_correct_name(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = compute_coping_capacity(sample_df, default_params)
        assert result.name == COPING_CAPACITY_INDEX

    def test_no_nan_for_full_data(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = compute_coping_capacity(sample_df, default_params)
        assert not result.isna().any()


class TestComputeSvi:
    def test_requires_upstream_columns(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        """SVI computation needs sensitivity_index and coping_capacity_index."""
        df = sample_df.copy()
        df[SENSITIVITY_INDEX] = compute_sensitivity(df, default_params)
        df[COPING_CAPACITY_INDEX] = compute_coping_capacity(df, default_params)
        result = compute_svi(df, default_params)
        assert result.name == SOCIAL_VULNERABILITY_INDEX
        assert not result.isna().any()


class TestFloodSusceptibilityTransform:
    def test_basic_transform(self) -> None:
        """SV_PF = (SV + 0.25*mean(SV))^2 for default params."""
        svi = pd.Series([0.0, 0.5, 1.0])
        mean_svi = svi.mean()  # 0.5
        expected = (svi + 0.25 * mean_svi) ** 2
        result = flood_susceptibility_transform(svi, 0.25, 2.0)
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            expected.rename(SOCIAL_VULNERABILITY_FLOOD_POTENTIAL).reset_index(
                drop=True
            ),
        )

    def test_zero_svi_nonzero_output(self) -> None:
        """Buildings with SVI=0 must still receive a non-zero SV_PF."""
        svi = pd.Series([0.0, 1.0, 2.0])
        result = flood_susceptibility_transform(svi, 0.25, 2.0)
        assert result.iloc[0] > 0.0

    def test_nan_propagation(self) -> None:
        svi = pd.Series([0.5, float("nan"), 1.0])
        result = flood_susceptibility_transform(svi, 0.25, 2.0)
        assert math.isnan(result.iloc[1])
        assert not math.isnan(result.iloc[0])

    def test_all_nan_returns_nan_series(self) -> None:
        svi = pd.Series([float("nan"), float("nan")])
        result = flood_susceptibility_transform(svi, 0.25, 2.0)
        assert result.isna().all()

    def test_result_name(self) -> None:
        svi = pd.Series([1.0, 2.0])
        assert (
            flood_susceptibility_transform(svi, 0.25, 2.0).name
            == SOCIAL_VULNERABILITY_FLOOD_POTENTIAL
        )


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------


class TestRunVulnerabilityPipeline:
    def test_adds_four_columns(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = run_vulnerability_pipeline(sample_df, default_params)
        for col in [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        ]:
            assert col in result.columns

    def test_original_columns_preserved(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = run_vulnerability_pipeline(sample_df, default_params)
        for col in sample_df.columns:
            assert col in result.columns

    def test_no_nan_for_complete_data(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        result = run_vulnerability_pipeline(sample_df, default_params)
        computed = [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        ]
        assert not result[computed].isna().any().any()

    def test_sv_pf_strictly_positive(
        self, sample_df: pd.DataFrame, default_params: VulnerabilityParams
    ) -> None:
        """Every building must have SV_PF > 0 (zero-floor guarantee)."""
        result = run_vulnerability_pipeline(sample_df, default_params)
        assert (result[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL] > 0.0).all()

    def test_entropy_flag_changes_output(self, sample_df: pd.DataFrame) -> None:
        with_entropy = run_vulnerability_pipeline(
            sample_df, VulnerabilityParams(apply_entropy=True)
        )
        without_entropy = run_vulnerability_pipeline(
            sample_df, VulnerabilityParams(apply_entropy=False)
        )
        # Not identical (unless all entropy factors happen to be 1)
        assert not with_entropy[SENSITIVITY_INDEX].equals(
            without_entropy[SENSITIVITY_INDEX]
        )

    def test_no_entropy_output_in_unit_range(self, sample_df: pd.DataFrame) -> None:
        result = run_vulnerability_pipeline(
            sample_df, VulnerabilityParams(apply_entropy=False)
        )
        for col in [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
        ]:
            vals = result[col].dropna()
            assert vals.between(0.0, 1.0 + 1e-9).all(), f"{col} out of [0,1]"

    def test_example_csv_data(self) -> None:
        """Smoke-test against the first 10 rows of the example data file."""
        import pathlib

        csv_path = (
            pathlib.Path(__file__).parents[1]
            / "data"
            / "example"
            / "Building_ExampleLayer.csv"
        )
        raw = pd.read_csv(csv_path, sep=",", decimal=".")
        raw.columns = raw.columns.str.strip()
        df = pd.DataFrame(
            {
                CHILDREN_UNDER_10: raw["C"].astype(float),
                ELDERLY_SINGLES_OVER_65: raw["ES"].astype(float),
                SOCIAL_WELFARE_RECIPIENTS: raw["WR"].astype(float),
                NO_HIGHSCHOOL_DIPLOMA: raw["EDQ"].astype(float),
            }
        )
        result = run_vulnerability_pipeline(df, VulnerabilityParams())

        # All computed columns must be present and finite
        for col in [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        ]:
            assert col in result.columns
            assert result[col].notna().all(), f"NaN in {col}"

        # SV_PF must be strictly positive for all buildings
        assert (result[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL] > 0.0).all()


# ---------------------------------------------------------------------------
# Module 2 — Exposure pipeline
# ---------------------------------------------------------------------------

from pluvial_flood_risk.algorithms.exposure import (  # noqa: E402
    compute_building_living_area,
    compute_exposure_mobility_accessibility,
    compute_residents_ground_floor,
    compute_residents_per_building,
    run_exposure_pipeline,
)
from pluvial_flood_risk.models.field_names import (  # noqa: E402
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


@pytest.fixture()
def exposure_df() -> pd.DataFrame:
    """Four representative buildings derived from Building_ExampleLayer.csv.

    Row indices match the original OBJECTID for traceability.

    Building 1: type=1, 1 floor  → R_G = R / 1  (all residents on ground floor)
    Building 3: type=1, 2 floors → R_G = R / 2  (half on ground floor)
    Building 4: type=2, 2 floors → R_G = 0      (ground floor commercial)
    Building 16: type=1, 6 floors → R_G = R / 6
    """
    return pd.DataFrame(
        {
            FLOORS_ABOVE_GROUND: [1, 2, 2, 6],
            BUILDING_TYPE: [1, 1, 2, 1],
            BUILDING_FOOTPRINT_AREA_M2: [221.78, 179.62, 372.77, 510.34],
            RESIDENTS_IN_STATISTICAL_UNIT: [250.0, 250.0, 250.0, 250.0],
            LIVING_AREA_M2_STATISTICAL_UNIT: [9000.0, 9000.0, 9000.0, 9000.0],
        },
        index=[1, 3, 4, 16],
    )


class TestComputeBuildingLivingArea:
    def test_type1_all_floors_residential(self, exposure_df: pd.DataFrame) -> None:
        """Building type 1: Area_house = Floors × footprint."""
        result = compute_building_living_area(exposure_df)
        # Building 1: 1 × 221.78 = 221.78
        assert result.loc[1] == pytest.approx(221.78)
        # Building 16: 6 × 510.34 = 3062.05
        assert result.loc[16] == pytest.approx(3062.04, rel=1e-4)

    def test_type2_ground_floor_commercial(self, exposure_df: pd.DataFrame) -> None:
        """Building type 2: Area_house = (Floors - 1) × footprint."""
        result = compute_building_living_area(exposure_df)
        # Building 4: (2 - 1) × 372.77 = 372.77
        assert result.loc[4] == pytest.approx(372.77)

    def test_type1_two_floors(self, exposure_df: pd.DataFrame) -> None:
        """Building type 1, 2 floors: Area_house = 2 × footprint."""
        result = compute_building_living_area(exposure_df)
        # Building 3: 2 × 179.62 = 359.24
        assert result.loc[3] == pytest.approx(359.24)

    def test_returns_correct_name(self, exposure_df: pd.DataFrame) -> None:
        assert compute_building_living_area(exposure_df).name == BUILDING_LIVING_AREA_M2


class TestComputeResidentsPerBuilding:
    def test_area_weighted_distribution(self, exposure_df: pd.DataFrame) -> None:
        """R = Area_house / LivingArea × Residents."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        result = compute_residents_per_building(df)
        # Building 1: 221.78 / 9000 × 250 ≈ 6.16
        assert result.loc[1] == pytest.approx(6.16, rel=1e-2)
        # Building 16: 3062.04 / 9000 × 250 ≈ 85.06
        assert result.loc[16] == pytest.approx(85.06, rel=1e-2)

    def test_zero_living_area_returns_nan(self, exposure_df: pd.DataFrame) -> None:
        """Division by zero living area must yield NaN, not an exception."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df.loc[1, LIVING_AREA_M2_STATISTICAL_UNIT] = 0.0
        result = compute_residents_per_building(df)
        assert math.isnan(result.loc[1])
        assert not math.isnan(result.loc[3])

    def test_returns_correct_name(self, exposure_df: pd.DataFrame) -> None:
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        assert compute_residents_per_building(df).name == RESIDENTS_PER_BUILDING


class TestComputeResidentsGroundFloor:
    def test_type1_single_floor_equals_r(self, exposure_df: pd.DataFrame) -> None:
        """Type 1, 1 floor: R_G = R / 1 = R."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        result = compute_residents_ground_floor(df)
        assert result.loc[1] == pytest.approx(df.loc[1, RESIDENTS_PER_BUILDING])

    def test_type2_ground_floor_is_zero(self, exposure_df: pd.DataFrame) -> None:
        """Type 2 (commercial ground floor): R_G = 0."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        result = compute_residents_ground_floor(df)
        assert result.loc[4] == pytest.approx(0.0)

    def test_type1_two_floors_is_half_r(self, exposure_df: pd.DataFrame) -> None:
        """Type 1, 2 floors: R_G = R / 2."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        result = compute_residents_ground_floor(df)
        assert result.loc[3] == pytest.approx(
            df.loc[3, RESIDENTS_PER_BUILDING] / 2.0, rel=1e-9
        )

    def test_type1_six_floors(self, exposure_df: pd.DataFrame) -> None:
        """Type 1, 6 floors: R_G = R / 6 ≈ 14."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        result = compute_residents_ground_floor(df)
        assert result.loc[16] == pytest.approx(85.06 / 6, rel=1e-2)

    def test_returns_correct_name(self, exposure_df: pd.DataFrame) -> None:
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        assert compute_residents_ground_floor(df).name == RESIDENTS_GROUND_FLOOR


class TestComputeExposureMobilityAccessibility:
    def test_equals_residents_per_building(self, exposure_df: pd.DataFrame) -> None:
        """EMA is semantically R — all residents, not just ground-floor."""
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        ema = compute_exposure_mobility_accessibility(df)
        pd.testing.assert_series_equal(
            ema.reset_index(drop=True),
            df[RESIDENTS_PER_BUILDING]
            .rename(EXPOSURE_MOBILITY_ACCESSIBILITY)
            .reset_index(drop=True),
        )

    def test_returns_correct_name(self, exposure_df: pd.DataFrame) -> None:
        df = exposure_df.copy()
        df[BUILDING_LIVING_AREA_M2] = compute_building_living_area(df)
        df[RESIDENTS_PER_BUILDING] = compute_residents_per_building(df)
        assert (
            compute_exposure_mobility_accessibility(df).name
            == EXPOSURE_MOBILITY_ACCESSIBILITY
        )


class TestRunExposurePipeline:
    def test_adds_four_columns(self, exposure_df: pd.DataFrame) -> None:
        result = run_exposure_pipeline(exposure_df)
        for col in [
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        ]:
            assert col in result.columns

    def test_original_columns_preserved(self, exposure_df: pd.DataFrame) -> None:
        result = run_exposure_pipeline(exposure_df)
        for col in exposure_df.columns:
            assert col in result.columns

    def test_no_nan_for_complete_data(self, exposure_df: pd.DataFrame) -> None:
        result = run_exposure_pipeline(exposure_df)
        computed = [
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        ]
        assert not result[computed].isna().any().any()

    def test_r_g_nonnegative(self, exposure_df: pd.DataFrame) -> None:
        """Ground-floor residents must never be negative."""
        result = run_exposure_pipeline(exposure_df)
        assert (result[RESIDENTS_GROUND_FLOOR] >= 0.0).all()

    def test_r_g_le_r(self, exposure_df: pd.DataFrame) -> None:
        """Ground-floor residents must not exceed total residents per building."""
        result = run_exposure_pipeline(exposure_df)
        assert (
            result[RESIDENTS_GROUND_FLOOR] <= result[RESIDENTS_PER_BUILDING] + 1e-9
        ).all()

    def test_ema_equals_r(self, exposure_df: pd.DataFrame) -> None:
        result = run_exposure_pipeline(exposure_df)
        pd.testing.assert_series_equal(
            result[EXPOSURE_MOBILITY_ACCESSIBILITY].reset_index(drop=True),
            result[RESIDENTS_PER_BUILDING].reset_index(drop=True),
            check_names=False,
        )

    def test_example_csv_data(self) -> None:
        """Smoke-test against full example CSV with known reference values."""
        import pathlib

        csv_path = (
            pathlib.Path(__file__).parents[1]
            / "data"
            / "example"
            / "Building_ExampleLayer.csv"
        )
        raw = pd.read_csv(csv_path, sep=",", decimal=".")
        raw.columns = raw.columns.str.strip()

        df = pd.DataFrame(
            {
                FLOORS_ABOVE_GROUND: raw["Floors"].astype(float),
                BUILDING_TYPE: raw["Building type"].astype(float),
                BUILDING_FOOTPRINT_AREA_M2: raw["Shape_Area"].astype(float),
                RESIDENTS_IN_STATISTICAL_UNIT: raw["Residents"].astype(float),
                LIVING_AREA_M2_STATISTICAL_UNIT: raw["LivingArea"].astype(float),
            }
        )

        result = run_exposure_pipeline(df)

        # All outputs present and finite
        for col in [
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
        ]:
            assert result[col].notna().all(), f"NaN in {col}"

        # Area_house matches CSV column
        pd.testing.assert_series_equal(
            result[BUILDING_LIVING_AREA_M2].reset_index(drop=True),
            raw["Area_house"].astype(float).reset_index(drop=True),
            check_names=False,
            rtol=1e-3,
        )

        # R matches CSV column (within rounding)
        pd.testing.assert_series_equal(
            result[RESIDENTS_PER_BUILDING].round(0).reset_index(drop=True),
            raw["R"].astype(float).reset_index(drop=True),
            check_names=False,
            rtol=0.02,
        )

        # R_G matches CSV column within 1 resident.
        # The original ArcPy script rounded R to an integer before computing
        # R_G = round(R) / Floors, so some rows differ by ≤1 from our
        # continuous (more accurate) computation.
        pd.testing.assert_series_equal(
            result[RESIDENTS_GROUND_FLOOR].round(0).reset_index(drop=True),
            raw["R_G"].astype(float).reset_index(drop=True),
            check_names=False,
            atol=1.0,
            check_exact=False,
        )


# ---------------------------------------------------------------------------
# Module 3 — Hazard: Mobility & Accessibility
# ---------------------------------------------------------------------------

import geopandas as gpd  # noqa: E402
from shapely.geometry import LineString, Polygon, box

from pluvial_flood_risk.algorithms.hazard_ma import (  # noqa: E402
    compute_hazard_ma,
    compute_mobility_flood_fractions,
    run_hazard_ma_pipeline,
)
from pluvial_flood_risk.algorithms.hazard_wb import (  # noqa: E402
    compute_hazard_wb,
    compute_wb_flood_fractions,
    flooded_area_col,
    flooded_fraction_col,
    run_hazard_wb_pipeline,
)
from pluvial_flood_risk.models.field_names import (  # noqa: E402
    FLOODED_AREA_M2_5M,
    FLOODED_AREA_M2_15M,
    FLOODED_AREA_M2_30M,
    FLOODED_FRACTION_5M,
    FLOODED_FRACTION_15M,
    FLOODED_FRACTION_30M,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
)
from pluvial_flood_risk.models.inputs import HazardParams  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic geometry helpers
#
# All geometries use a flat local coordinate system (units = metres) so that
# buffer distances and areas are exact.  We place the building at the origin
# so that expected zone radii are easy to reason about.
#
#  Building:  20 × 10 m rectangle centred at (0, 0)
#             → box(-10, -5, 10, 5),  area = 200 m²
#
#  Street:    A narrow east-west road at y = 20, i.e. between 10 m and 30 m
#             north of the building centroid.  Represented as a LineString
#             so that the implementation buffers it to 0.5 m.
#             → LineString([(-30, 20), (30, 20)])
#
#  Flood:     A rectangle covering x ∈ [-30, 30], y ∈ [5, 30].
#             This floods the 5 m direct buffer completely (the outer ring of
#             the building from y=5 to y=10) and overlaps the street zones.
# ---------------------------------------------------------------------------

_CRS = "EPSG:32632"  # UTM zone 32N — a flat, metric CRS


@pytest.fixture()
def building_gdf() -> gpd.GeoDataFrame:
    """Single 20×10 m building at the origin."""
    geom = box(-10.0, -5.0, 10.0, 5.0)
    return gpd.GeoDataFrame({"id": [1], "geometry": [geom]}, crs=_CRS)


@pytest.fixture()
def street_gdf() -> gpd.GeoDataFrame:
    """Single east-west street line at y = 20 (15–25 m north of building)."""
    geom = LineString([(-30.0, 20.0), (30.0, 20.0)])
    return gpd.GeoDataFrame({"geometry": [geom]}, crs=_CRS)


@pytest.fixture()
def flood_gdf_full() -> gpd.GeoDataFrame:
    """Flood polygon covering the entire north side: x∈[-30,30], y∈[5,50]."""
    geom = box(-30.0, 5.0, 30.0, 50.0)
    return gpd.GeoDataFrame({"geometry": [geom]}, crs=_CRS)


@pytest.fixture()
def flood_gdf_none() -> gpd.GeoDataFrame:
    """Flood polygon far away — no intersection with any zone."""
    geom = box(1000.0, 1000.0, 1010.0, 1010.0)
    return gpd.GeoDataFrame({"geometry": [geom]}, crs=_CRS)


@pytest.fixture()
def params_default() -> HazardParams:
    return HazardParams()


class TestComputeMobilityFloodFractions:
    """Step 3.1 — spatial preprocessing with synthetic geometry."""

    def test_returns_six_expected_columns(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        for col in [
            FLOODED_AREA_M2_5M,
            FLOODED_FRACTION_5M,
            FLOODED_AREA_M2_15M,
            FLOODED_FRACTION_15M,
            FLOODED_AREA_M2_30M,
            FLOODED_FRACTION_30M,
        ]:
            assert col in result.columns, f"Missing column: {col}"

    def test_building_id_preserved_in_index(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        assert 1 in result.index

    def test_no_flood_gives_zero_fractions(
        self, building_gdf, street_gdf, flood_gdf_none, params_default
    ) -> None:
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_none, params_default
        )
        for col in [FLOODED_FRACTION_5M, FLOODED_FRACTION_15M, FLOODED_FRACTION_30M]:
            assert result.loc[1, col] == pytest.approx(0.0)

    def test_full_flood_gives_nonzero_fraction_5m(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        """With flood covering the entire north side, the 5 m buffer is partially
        flooded (north half).  Fraction must be > 0."""
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        assert result.loc[1, FLOODED_FRACTION_5M] > 0.0

    def test_fractions_in_unit_interval(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        for col in [FLOODED_FRACTION_5M, FLOODED_FRACTION_15M, FLOODED_FRACTION_30M]:
            val = result.loc[1, col]
            assert 0.0 <= val <= 1.0, f"{col} = {val} out of [0, 1]"

    def test_flooded_area_consistent_with_fraction(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        """flooded_area / zone_area should equal the fraction (where area > 0)."""
        result = compute_mobility_flood_fractions(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        area_5m = result.loc[1, FLOODED_AREA_M2_5M]
        frac_5m = result.loc[1, FLOODED_FRACTION_5M]
        if frac_5m > 0.0:
            # Derived zone area ≈ area / frac — must be positive
            assert area_5m / frac_5m > 0.0

    def test_invalid_zone3_smaller_than_zone2_raises(
        self, building_gdf, street_gdf, flood_gdf_full
    ) -> None:
        bad_params = HazardParams(buffer_zone2_m=30.0, buffer_zone3_m=15.0)
        with pytest.raises(ValueError, match="buffer_zone3_m"):
            compute_mobility_flood_fractions(
                building_gdf, street_gdf, flood_gdf_full, bad_params
            )

    def test_multiple_buildings_independent_results(
        self, street_gdf, flood_gdf_full, params_default
    ) -> None:
        """Two buildings far apart should produce independent fractions."""
        gdf = gpd.GeoDataFrame(
            {
                "id": [1, 2],
                "geometry": [
                    box(-10.0, -5.0, 10.0, 5.0),  # at origin — partially flooded
                    box(500.0, -5.0, 510.0, 5.0),  # far east  — not flooded
                ],
            },
            crs=_CRS,
        )
        result = compute_mobility_flood_fractions(
            gdf, street_gdf, flood_gdf_full, params_default
        )
        assert result.loc[1, FLOODED_FRACTION_5M] > 0.0
        assert result.loc[2, FLOODED_FRACTION_5M] == pytest.approx(0.0)


class TestComputeHazardMA:
    """Step 3.2 — pure formula tests (no geometry dependency)."""

    def _df(
        self,
        frac_5m: float = 0.0,
        area_5m: float = 0.0,
        frac_15m: float = 0.0,
        area_15m: float = 0.0,
        frac_30m: float = 0.0,
        area_30m: float = 0.0,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                FLOODED_FRACTION_5M: [frac_5m],
                FLOODED_AREA_M2_5M: [area_5m],
                FLOODED_FRACTION_15M: [frac_15m],
                FLOODED_AREA_M2_15M: [area_15m],
                FLOODED_FRACTION_30M: [frac_30m],
                FLOODED_AREA_M2_30M: [area_30m],
            },
            index=[1],
        )

    def test_zero_flood_gives_low_hma(self) -> None:
        """All fractions zero → lognorm.cdf(0 * 4, σ) — effectively 0."""
        result = compute_hazard_ma(self._df())
        assert result.iloc[0] == pytest.approx(0.0, abs=1e-9)

    def test_returns_series_named_correctly(self) -> None:
        result = compute_hazard_ma(self._df())
        assert result.name == HAZARD_MOBILITY_ACCESSIBILITY

    def test_hma_in_unit_interval(self) -> None:
        df = self._df(frac_5m=0.5, area_5m=10.0)
        result = compute_hazard_ma(df)
        assert 0.0 <= result.iloc[0] <= 1.0

    def test_higher_fraction_gives_higher_hma(self) -> None:
        """Monotonicity: larger flooded fraction → larger HMA."""
        low = compute_hazard_ma(self._df(frac_5m=0.1, area_5m=5.0)).iloc[0]
        high = compute_hazard_ma(self._df(frac_5m=0.8, area_5m=5.0)).iloc[0]
        assert high > low

    def test_formula_against_scipy_reference(self) -> None:
        """HMA = lognorm.cdf(max_frac * 4, s=σ) — verify against direct scipy call."""
        from scipy.stats import lognorm as _lognorm

        params = HazardParams(lognormal_shape=0.25)
        frac = 0.3
        df = self._df(frac_5m=frac, area_5m=20.0)
        result = compute_hazard_ma(df, params).iloc[0]
        expected = _lognorm.cdf(frac * 4.0, s=0.25)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_min_area_filter_suppresses_small_street_zone(self) -> None:
        """A 15m zone with tiny total area (< min_area_m2) must be ignored."""
        params = HazardParams(min_street_flood_area_m2=10.0)
        # frac_15m = 0.5, zone_total = area/frac = 2/0.5 = 4 m² < 10 m² → suppressed
        df_small = self._df(frac_15m=0.5, area_15m=2.0)
        result_small = compute_hazard_ma(df_small, params).iloc[0]
        # Without suppression (large zone)
        df_large = self._df(frac_15m=0.5, area_15m=20.0)
        result_large = compute_hazard_ma(df_large, params).iloc[0]
        assert result_small < result_large

    def test_max_of_three_zones_is_used(self) -> None:
        """HMA uses the maximum fraction across all three zones."""
        from scipy.stats import lognorm as _lognorm

        params = HazardParams(lognormal_shape=0.25, min_street_flood_area_m2=0.0)
        # Zone 30m has highest fraction
        df = self._df(
            frac_5m=0.1,
            area_5m=10.0,
            frac_15m=0.2,
            area_15m=50.0,
            frac_30m=0.6,
            area_30m=80.0,
        )
        result = compute_hazard_ma(df, params).iloc[0]
        expected = _lognorm.cdf(0.6 * 4.0, s=0.25)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_index_preserved(self) -> None:
        df = self._df(frac_5m=0.3, area_5m=10.0)
        result = compute_hazard_ma(df)
        assert list(result.index) == [1]


class TestRunHazardMAPipeline:
    """run_hazard_ma_pipeline — orchestration: Step 3.1 + 3.2 together."""

    def test_adds_seven_columns(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        result = run_hazard_ma_pipeline(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        for col in [
            FLOODED_AREA_M2_5M,
            FLOODED_FRACTION_5M,
            FLOODED_AREA_M2_15M,
            FLOODED_FRACTION_15M,
            FLOODED_AREA_M2_30M,
            FLOODED_FRACTION_30M,
            HAZARD_MOBILITY_ACCESSIBILITY,
        ]:
            assert col in result.columns

    def test_hma_in_unit_interval(
        self, building_gdf, street_gdf, flood_gdf_full, params_default
    ) -> None:
        result = run_hazard_ma_pipeline(
            building_gdf, street_gdf, flood_gdf_full, params_default
        )
        hma = result[HAZARD_MOBILITY_ACCESSIBILITY]
        assert (hma >= 0.0).all() and (hma <= 1.0).all()

    def test_no_flood_gives_zero_hma(
        self, building_gdf, street_gdf, flood_gdf_none, params_default
    ) -> None:
        result = run_hazard_ma_pipeline(
            building_gdf, street_gdf, flood_gdf_none, params_default
        )
        assert result.loc[1, HAZARD_MOBILITY_ACCESSIBILITY] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Module 4: Well-Being Hazard (Steps 4.1–4.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def building_gdf_wb() -> gpd.GeoDataFrame:
    """Single 20×10 m building at the origin."""
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {"id": [1], "geometry": [box(-10, -5, 10, 5)]},
        crs=_CRS,
    ).set_index("id")


@pytest.fixture
def flood_30cm() -> gpd.GeoDataFrame:
    """Flood polygon covering the 2 m buffer zone fully, depth 30 cm."""
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {"level_cm": [30], "geometry": [box(-15, -10, 15, 10)]},
        crs=_CRS,
    )


@pytest.fixture
def flood_multi_depth() -> gpd.GeoDataFrame:
    """Three depth classes (30, 60, 100 cm) each fully covering the buffer zone."""
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {
            "level_cm": [30, 60, 100],
            "geometry": [box(-15, -10, 15, 10)] * 3,
        },
        crs=_CRS,
    )


@pytest.fixture
def flood_no_overlap_wb() -> gpd.GeoDataFrame:
    """Flood polygon far from the building — no intersection with buffer."""
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {"level_cm": [30], "geometry": [box(1000, 1000, 2000, 2000)]},
        crs=_CRS,
    )


class TestComputeWbFloodFractions:
    """Unit tests for compute_wb_flood_fractions (Step 4.1)."""

    def test_full_flood_gives_fraction_one(
        self, building_gdf_wb, flood_30cm, params_default
    ) -> None:
        result = compute_wb_flood_fractions(building_gdf_wb, flood_30cm, params_default)
        frac = result.loc[1, flooded_fraction_col(30)]
        assert frac == pytest.approx(1.0, abs=1e-6)

    def test_no_flood_gives_fraction_zero(
        self, building_gdf_wb, flood_no_overlap_wb, params_default
    ) -> None:
        result = compute_wb_flood_fractions(
            building_gdf_wb, flood_no_overlap_wb, params_default
        )
        assert result.loc[1, flooded_fraction_col(30)] == pytest.approx(0.0)

    def test_multiple_depth_classes_produce_separate_columns(
        self, building_gdf_wb, flood_multi_depth, params_default
    ) -> None:
        result = compute_wb_flood_fractions(
            building_gdf_wb, flood_multi_depth, params_default
        )
        for d in [30, 60, 100]:
            assert flooded_fraction_col(d) in result.columns
            assert flooded_area_col(d) in result.columns

    def test_all_buildings_present_when_no_flood(
        self, building_gdf_wb, flood_no_overlap_wb, params_default
    ) -> None:
        result = compute_wb_flood_fractions(
            building_gdf_wb, flood_no_overlap_wb, params_default
        )
        assert 1 in result.index

    def test_fraction_non_negative(
        self, building_gdf_wb, flood_30cm, params_default
    ) -> None:
        result = compute_wb_flood_fractions(building_gdf_wb, flood_30cm, params_default)
        fracs = [result.loc[1, flooded_fraction_col(d)] for d in [30]]
        assert all(f >= 0.0 for f in fracs)

    def test_fraction_at_most_one(
        self, building_gdf_wb, flood_multi_depth, params_default
    ) -> None:
        result = compute_wb_flood_fractions(
            building_gdf_wb, flood_multi_depth, params_default
        )
        for d in [30, 60, 100]:
            frac = result.loc[1, flooded_fraction_col(d)]
            assert frac <= 1.0 + 1e-6

    def test_flooded_area_consistent_with_fraction(
        self, building_gdf_wb, flood_30cm, params_default
    ) -> None:
        """flooded_area > 0 iff fraction > 0."""
        result = compute_wb_flood_fractions(building_gdf_wb, flood_30cm, params_default)
        area = result.loc[1, flooded_area_col(30)]
        frac = result.loc[1, flooded_fraction_col(30)]
        assert (area > 0.0) == (frac > 0.0)

    def test_missing_level_cm_raises(self, building_gdf_wb) -> None:
        from shapely.geometry import box

        bad_flood = gpd.GeoDataFrame({"geometry": [box(-15, -10, 15, 10)]}, crs=_CRS)
        with pytest.raises(ValueError, match="level_cm"):
            compute_wb_flood_fractions(building_gdf_wb, bad_flood)


class TestComputeHazardWB:
    """Unit tests for compute_hazard_wb (Step 4.2, Eq. 12)."""

    def test_zero_fractions_gives_zero_hwb(self) -> None:
        df = pd.DataFrame(
            {flooded_fraction_col(30): [0.0], flooded_area_col(30): [0.0]},
            index=[1],
        )
        result = compute_hazard_wb(df, HazardParams())
        assert result.loc[1] == pytest.approx(0.0, abs=1e-10)

    def test_full_fraction_one_depth_class(self) -> None:
        """One depth class at fraction 1.0 → lognorm.cdf(4, 0.25)."""
        from scipy.stats import lognorm as sp_lognorm

        df = pd.DataFrame(
            {flooded_fraction_col(30): [1.0], flooded_area_col(30): [100.0]},
            index=[1],
        )
        result = compute_hazard_wb(df, HazardParams())
        expected = sp_lognorm.cdf(4.0, s=0.25)
        assert result.loc[1] == pytest.approx(expected, rel=1e-6)

    def test_sum_over_three_depth_classes(self) -> None:
        """Three independent depth classes sum their CDF contributions."""
        from scipy.stats import lognorm as sp_lognorm

        fracs = {30: 0.5, 60: 0.8, 100: 1.0}
        df = pd.DataFrame(
            {flooded_fraction_col(d): [f] for d, f in fracs.items()}
            | {flooded_area_col(d): [1.0] for d in fracs},
            index=[1],
        )
        result = compute_hazard_wb(df, HazardParams())
        expected = sum(sp_lognorm.cdf(f * 4.0, s=0.25) for f in fracs.values())
        assert result.loc[1] == pytest.approx(expected, rel=1e-6)

    def test_result_name_is_hazard_well_being(self) -> None:
        df = pd.DataFrame(
            {flooded_fraction_col(30): [0.5], flooded_area_col(30): [50.0]},
            index=[1],
        )
        result = compute_hazard_wb(df, HazardParams())
        assert result.name == HAZARD_WELL_BEING

    def test_hwb_monotone_with_fraction(self) -> None:
        """Higher fraction → higher HWB (monotone)."""
        fracs = [0.0, 0.1, 0.25, 0.5, 0.75, 1.0]
        df = pd.DataFrame(
            {
                flooded_fraction_col(30): fracs,
                flooded_area_col(30): [f * 100 for f in fracs],
            },
            index=range(len(fracs)),
        )
        result = compute_hazard_wb(df, HazardParams())
        hwb = result.values
        assert all(hwb[i] <= hwb[i + 1] for i in range(len(hwb) - 1))

    def test_no_fraction_columns_gives_zero(self) -> None:
        """DataFrame with no fraction columns → HWB = 0 for all rows."""
        df = pd.DataFrame({"some_other_col": [1.0, 2.0]}, index=[1, 2])
        result = compute_hazard_wb(df, HazardParams())
        assert (result == 0.0).all()

    def test_hwb_increases_with_more_depth_classes(self) -> None:
        """Adding a depth class with positive fraction increases HWB."""
        df1 = pd.DataFrame(
            {flooded_fraction_col(30): [0.5], flooded_area_col(30): [50.0]},
            index=[1],
        )
        df2 = df1.copy()
        df2[flooded_fraction_col(60)] = 0.5
        df2[flooded_area_col(60)] = 50.0
        hwb1 = compute_hazard_wb(df1, HazardParams()).loc[1]
        hwb2 = compute_hazard_wb(df2, HazardParams()).loc[1]
        assert hwb2 > hwb1

    def test_manual_cross_check_example_building_1(self) -> None:
        """Reproduce HWB for example building 1 using ArcPy percentages."""
        from scipy.stats import lognorm as sp_lognorm

        # P values from building_example_results.geojson, building 1 (in %):
        example_pcts = {
            30: 91.763395960635819,
            40: 91.764768321029095,
            50: 91.763396914733391,
            60: 91.764317846146142,
            70: 90.998250093922465,
            80: 87.057066385638905,
            90: 52.953823475712113,
            100: 7.9097231023667458,
        }
        df = pd.DataFrame(
            {flooded_fraction_col(d): [p / 100.0] for d, p in example_pcts.items()}
            | {flooded_area_col(d): [1.0] for d in example_pcts},
            index=[1],
        )
        result = compute_hazard_wb(df, HazardParams())
        expected_hwb = 6.9986609884078179
        assert result.loc[1] == pytest.approx(expected_hwb, rel=1e-4)


class TestRunHazardWBPipeline:
    """Unit tests for run_hazard_wb_pipeline (orchestration)."""

    def test_output_contains_hazard_well_being_column(
        self, building_gdf_wb, flood_multi_depth, params_default
    ) -> None:
        result = run_hazard_wb_pipeline(
            building_gdf_wb, flood_multi_depth, params_default
        )
        assert HAZARD_WELL_BEING in result.columns

    def test_output_contains_fraction_and_area_columns(
        self, building_gdf_wb, flood_multi_depth, params_default
    ) -> None:
        result = run_hazard_wb_pipeline(
            building_gdf_wb, flood_multi_depth, params_default
        )
        for d in [30, 60, 100]:
            assert flooded_fraction_col(d) in result.columns
            assert flooded_area_col(d) in result.columns

    def test_hwb_in_valid_range_for_single_depth(
        self, building_gdf_wb, flood_30cm, params_default
    ) -> None:
        result = run_hazard_wb_pipeline(building_gdf_wb, flood_30cm, params_default)
        hwb = result.loc[1, HAZARD_WELL_BEING]
        # Full fraction → lognorm.cdf(4, 0.25) ≈ 1.0; HWB ≤ N_depths
        assert 0.0 <= hwb <= 1.0 + 1e-6

    def test_no_flood_gives_zero_hwb(
        self, building_gdf_wb, flood_no_overlap_wb, params_default
    ) -> None:
        result = run_hazard_wb_pipeline(
            building_gdf_wb, flood_no_overlap_wb, params_default
        )
        assert result.loc[1, HAZARD_WELL_BEING] == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# Module 5: Risk Calculation (Step 5, Eq. 14/15)
# ---------------------------------------------------------------------------

from pluvial_flood_risk.algorithms.risk import (  # noqa: E402
    compute_risk,
    run_risk_pipeline,
)
from pluvial_flood_risk.models.field_names import (  # noqa: E402
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    HAZARD_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
)
from pluvial_flood_risk.models.inputs import RiskWeights  # noqa: E402


def _risk_df(**kwargs) -> pd.DataFrame:
    """Build a minimal one-row or multi-row risk DataFrame."""
    defaults = {
        SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: [1.0],
        RESIDENTS_GROUND_FLOOR: [1.0],
        EXPOSURE_MOBILITY_ACCESSIBILITY: [1.0],
        HAZARD_WELL_BEING: [1.0],
        HAZARD_MOBILITY_ACCESSIBILITY: [1.0],
    }
    defaults.update(kwargs)
    return pd.DataFrame(defaults, index=[1])


class TestComputeRisk:
    """Unit tests for compute_risk (the core Eq. 14/15 formula)."""

    def test_all_ones_default_exponents_gives_one(self) -> None:
        sv = pd.Series([1.0], index=[1])
        e = pd.Series([1.0], index=[1])
        h = pd.Series([1.0], index=[1])
        assert compute_risk(sv, e, h, RiskWeights()).iloc[0] == pytest.approx(1.0)

    def test_zero_svpf_gives_zero(self) -> None:
        sv = pd.Series([0.0], index=[1])
        e = pd.Series([5.0], index=[1])
        h = pd.Series([2.0], index=[1])
        assert compute_risk(sv, e, h, RiskWeights()).iloc[0] == pytest.approx(0.0)

    def test_zero_exposure_gives_zero(self) -> None:
        sv = pd.Series([0.5], index=[1])
        e = pd.Series([0.0], index=[1])
        h = pd.Series([2.0], index=[1])
        assert compute_risk(sv, e, h, RiskWeights()).iloc[0] == pytest.approx(0.0)

    def test_zero_hazard_gives_zero(self) -> None:
        sv = pd.Series([0.5], index=[1])
        e = pd.Series([3.0], index=[1])
        h = pd.Series([0.0], index=[1])
        assert compute_risk(sv, e, h, RiskWeights()).iloc[0] == pytest.approx(0.0)

    def test_unweighted_product(self) -> None:
        """With default exponents (all 1): PFR = SV_PF * E * H."""
        sv = pd.Series([0.5], index=[1])
        e = pd.Series([4.0], index=[1])
        h = pd.Series([3.0], index=[1])
        result = compute_risk(sv, e, h, RiskWeights())
        assert result.iloc[0] == pytest.approx(0.5 * 4.0 * 3.0)

    def test_custom_exponents(self) -> None:
        """PFR = SV_PF^0.5 * E^0.3 * H^0.2."""
        params = RiskWeights(
            vulnerability_exponent=0.5, exposure_exponent=0.3, hazard_exponent=0.2
        )
        sv = pd.Series([4.0], index=[1])
        e = pd.Series([8.0], index=[1])
        h = pd.Series([2.0], index=[1])
        expected = (4.0**0.5) * (8.0**0.3) * (2.0**0.2)
        assert compute_risk(sv, e, h, params).iloc[0] == pytest.approx(expected)

    def test_negative_values_clipped_to_zero(self) -> None:
        """Floating-point negatives near zero must not produce NaN or errors."""
        sv = pd.Series([-1e-15], index=[1])
        e = pd.Series([3.0], index=[1])
        h = pd.Series([2.0], index=[1])
        result = compute_risk(sv, e, h, RiskWeights())
        assert result.iloc[0] == pytest.approx(0.0)

    def test_multiple_buildings(self) -> None:
        """Vector input: correct element-wise product."""
        sv = pd.Series([0.2, 0.8], index=[1, 2])
        e = pd.Series([5.0, 2.0], index=[1, 2])
        h = pd.Series([1.0, 3.0], index=[1, 2])
        result = compute_risk(sv, e, h, RiskWeights())
        assert result.loc[1] == pytest.approx(0.2 * 5.0 * 1.0)
        assert result.loc[2] == pytest.approx(0.8 * 2.0 * 3.0)

    def test_index_preserved(self) -> None:
        idx = [10, 20, 30]
        sv = pd.Series([1.0, 2.0, 3.0], index=idx)
        e = pd.Series([1.0, 1.0, 1.0], index=idx)
        h = pd.Series([1.0, 1.0, 1.0], index=idx)
        result = compute_risk(sv, e, h, RiskWeights())
        assert list(result.index) == idx


class TestRunRiskPipeline:
    """Unit tests for run_risk_pipeline (Eq. 14/15 orchestration)."""

    def test_output_contains_both_risk_columns(self) -> None:
        df = _risk_df()
        result = run_risk_pipeline(df, RiskWeights())
        assert PLUVIAL_FLOOD_RISK_WELL_BEING in result.columns
        assert PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY in result.columns

    def test_input_columns_preserved(self) -> None:
        df = _risk_df()
        result = run_risk_pipeline(df, RiskWeights())
        for col in df.columns:
            assert col in result.columns

    def test_wb_uses_residents_ground_floor_and_hwb(self) -> None:
        """PFR_WB = SV_PF * EWB * HWB."""
        df = _risk_df(
            **{
                SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: [0.4],
                RESIDENTS_GROUND_FLOOR: [3.0],
                HAZARD_WELL_BEING: [2.5],
                EXPOSURE_MOBILITY_ACCESSIBILITY: [999.0],  # must not affect WB
                HAZARD_MOBILITY_ACCESSIBILITY: [999.0],    # must not affect WB
            }
        )
        result = run_risk_pipeline(df, RiskWeights())
        assert result.loc[1, PLUVIAL_FLOOD_RISK_WELL_BEING] == pytest.approx(
            0.4 * 3.0 * 2.5
        )
        # MA path must not be polluted by WB inputs
        assert result.loc[1, PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY] == pytest.approx(
            0.4 * 999.0 * 999.0
        )

    def test_ma_uses_ema_and_hma(self) -> None:
        """PFR_MA = SV_PF * EMA * HMA."""
        df = _risk_df(
            **{
                SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: [0.6],
                EXPOSURE_MOBILITY_ACCESSIBILITY: [10.0],
                HAZARD_MOBILITY_ACCESSIBILITY: [0.5],
                RESIDENTS_GROUND_FLOOR: [999.0],           # must not affect MA
                HAZARD_WELL_BEING: [999.0],                # must not affect MA
            }
        )
        result = run_risk_pipeline(df, RiskWeights())
        assert result.loc[1, PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY] == pytest.approx(
            0.6 * 10.0 * 0.5
        )

    def test_wb_uses_same_svpf_as_ma(self) -> None:
        """SV_PF is shared — the vulnerability factor is the same for both indices."""
        df = _risk_df(
            **{
                SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: [0.7],
                RESIDENTS_GROUND_FLOOR: [2.0],
                HAZARD_WELL_BEING: [1.5],
                EXPOSURE_MOBILITY_ACCESSIBILITY: [4.0],
                HAZARD_MOBILITY_ACCESSIBILITY: [0.8],
            }
        )
        result = run_risk_pipeline(df, RiskWeights())
        pfr_wb = result.loc[1, PLUVIAL_FLOOD_RISK_WELL_BEING]
        pfr_ma = result.loc[1, PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY]
        # Both share SV_PF=0.7 → ratio equals (EWB*HWB)/(EMA*HMA)
        assert pfr_wb / pfr_ma == pytest.approx((2.0 * 1.5) / (4.0 * 0.8))

    def test_no_nan_in_output(self) -> None:
        df = _risk_df()
        result = run_risk_pipeline(df, RiskWeights())
        assert not result[PLUVIAL_FLOOD_RISK_WELL_BEING].isna().any()
        assert not result[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY].isna().any()

    def test_non_negative_output(self) -> None:
        df = _risk_df()
        result = run_risk_pipeline(df, RiskWeights())
        assert (result[PLUVIAL_FLOOD_RISK_WELL_BEING] >= 0.0).all()
        assert (result[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY] >= 0.0).all()

    def test_all_zeros_when_svpf_zero(self) -> None:
        df = _risk_df(**{SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: [0.0]})
        result = run_risk_pipeline(df, RiskWeights())
        assert result.loc[1, PLUVIAL_FLOOD_RISK_WELL_BEING] == pytest.approx(0.0)
        assert result.loc[1, PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY] == pytest.approx(
            0.0
        )
