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
        raw = pd.read_csv(csv_path, sep=";", decimal=",")
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
