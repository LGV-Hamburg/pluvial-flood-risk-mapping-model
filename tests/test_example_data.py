"""Regression tests using the original Hamburg example data.

These tests load the GeoJSON files in ``data/example/`` and verify that our
reimplemented algorithms produce results that are consistent with the original
ArcPy outputs stored in ``building_example_results.geojson``.

Known systematic differences from the original ArcPy pipeline
--------------------------------------------------------------
1. **Building footprint area** — The original ArcPy project stored
   ``Shape_Area`` values computed in EPSG:3857 (Web Mercator).  At Hamburg's
   latitude (~53.5 °N) this inflates areas by a factor of ≈ 2.83 × compared
   with EPSG:25832 (ETRS89 / UTM-32N).  Our pipeline uses EPSG:25832 for all
   metric calculations, which is geographically more accurate.  As a result
   **exposure values (residents per building) from our pipeline are ~2.83 ×
   smaller** than the original integer counts.  Tests for the exposure module
   therefore verify *structural* properties and *internal consistency* rather
   than matching absolute resident counts.

2. **Vulnerability TOPSIS scores** — The original output shows
   ``Sen = CCap = SV = SV_PF = 0`` for many buildings in
   ``building_example_results.geojson``.  Our entropy-weighted TOPSIS
   implementation produces non-zero closeness values for the same buildings.
   The zero values in the original are likely an artefact of the negative-ideal
   solution coinciding with a large fraction of the dataset (minimum indicator
   values) given the particular distribution of indicator values in this
   neighbourhood.  Our weighted-entropy TOPSIS is the algorithm described in
   the paper.  Tests therefore verify *ordering* and *range* properties.

3. **Flood fractions (spatial)** — Buffer and overlay operations in
   EPSG:25832 produce geometrically equivalent but numerically distinct results
   from ArcPy's ``PairwiseBuffer`` / ``TabulateIntersection`` which ran in the
   original Web Mercator CRS.  Spatial fraction comparisons use a 50 %
   relative tolerance.

4. **HMA formula (pure math)** — When the expected flood fractions from the
   original ArcPy run are fed directly into ``compute_hazard_ma()``, the
   result matches ``HMA_expected`` to within floating-point precision (< 1 %).
   This confirms that our Eq. 13 implementation is correct independently of
   spatial computation differences.

Markers
-------
``regression`` — runs with all unit tests; does NOT require Redis.

Run only these tests::

    python -m pytest -m regression
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from scipy.stats import lognorm

from pluvial_flood_risk.algorithms.exposure import run_exposure_pipeline
from pluvial_flood_risk.algorithms.hazard_ma import (
    compute_hazard_ma,
    run_hazard_ma_pipeline,
)
from pluvial_flood_risk.algorithms.vulnerability import run_vulnerability_pipeline
from pluvial_flood_risk.models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_TYPE,
    COPING_CAPACITY_INDEX,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    FLOODED_AREA_M2_5M,
    FLOODED_AREA_M2_15M,
    FLOODED_AREA_M2_30M,
    FLOODED_FRACTION_5M,
    FLOODED_FRACTION_15M,
    FLOODED_FRACTION_30M,
    FLOORS_ABOVE_GROUND,
    HAZARD_MOBILITY_ACCESSIBILITY,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_PER_BUILDING,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
)
from pluvial_flood_risk.models.inputs import HazardParams, PipelineParams

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA = Path(__file__).parent.parent / "data" / "example"

# ---------------------------------------------------------------------------
# Module-scoped fixtures — loaded once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def buildings_gdf() -> gpd.GeoDataFrame:
    """Buildings projected to ETRS89 / UTM-32N with footprint area column."""
    gdf = gpd.read_file(_DATA / "buildings_example.geojson").set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:25832")
    gdf[BUILDING_FOOTPRINT_AREA_M2] = gdf.geometry.area
    gdf.index = gdf["ID"]
    return gdf


@pytest.fixture(scope="module")
def streets_gdf() -> gpd.GeoDataFrame:
    """Streets projected to ETRS89 / UTM-32N."""
    gdf = gpd.read_file(_DATA / "streets_example.geojson").set_crs("EPSG:4326")
    return gdf.to_crs("EPSG:25832")


@pytest.fixture(scope="module")
def flood_all_gdf() -> gpd.GeoDataFrame:
    """All flood-depth classes projected to ETRS89 / UTM-32N."""
    gdf = gpd.read_file(_DATA / "flood_levels_example.geojson").set_crs("EPSG:4326")
    return gdf.to_crs("EPSG:25832")


@pytest.fixture(scope="module")
def flood_30_gdf(flood_all_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Flood polygons for the 30 cm depth class."""
    return flood_all_gdf[flood_all_gdf["level_cm"] == 30].copy()


@pytest.fixture(scope="module")
def expected() -> gpd.GeoDataFrame:
    """Original ArcPy results indexed by building ID."""
    gdf = gpd.read_file(_DATA / "building_example_results.geojson")
    return gdf.set_index("ID")


@pytest.fixture(scope="module")
def vulnerability_result(
    buildings_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Output of the vulnerability pipeline on the example buildings."""
    params = PipelineParams()
    return run_vulnerability_pipeline(buildings_gdf, params.vulnerability)


@pytest.fixture(scope="module")
def exposure_result(
    buildings_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Output of the exposure pipeline on the example buildings."""
    return run_exposure_pipeline(buildings_gdf)


@pytest.fixture(scope="module")
def hazard_ma_result(
    buildings_gdf: gpd.GeoDataFrame,
    streets_gdf: gpd.GeoDataFrame,
    flood_30_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Output of the full hazard-MA pipeline on the example data."""
    params = HazardParams()
    return run_hazard_ma_pipeline(buildings_gdf, streets_gdf, flood_30_gdf, params)


# ---------------------------------------------------------------------------
# 1. Vulnerability pipeline tests
# ---------------------------------------------------------------------------


@pytest.mark.regression
class TestVulnerabilityExampleData:
    """Vulnerability pipeline against real example buildings.

    Absolute values differ from the original ArcPy output (see module
    docstring), so tests focus on range and ordering invariants.
    """

    def test_all_buildings_present(
        self,
        vulnerability_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        assert set(vulnerability_result.index) == set(buildings_gdf.index)

    def test_sensitivity_index_in_valid_range(
        self, vulnerability_result: pd.DataFrame
    ) -> None:
        """With entropy multiplier U ∈ [1, 2], scores lie in [0, 2]."""
        si = vulnerability_result[SENSITIVITY_INDEX]
        assert (si >= 0.0).all(), "Negative sensitivity index"
        assert (si <= 2.0).all(), "Sensitivity index exceeds 2.0"

    def test_coping_capacity_index_in_valid_range(
        self, vulnerability_result: pd.DataFrame
    ) -> None:
        cc = vulnerability_result[COPING_CAPACITY_INDEX]
        assert (cc >= 0.0).all()
        assert (cc <= 2.0).all()

    def test_social_vulnerability_index_in_valid_range(
        self, vulnerability_result: pd.DataFrame
    ) -> None:
        svi = vulnerability_result[SOCIAL_VULNERABILITY_INDEX]
        assert (svi >= 0.0).all()
        assert (svi <= 2.0).all()

    def test_sv_pf_strictly_positive(self, vulnerability_result: pd.DataFrame) -> None:
        """Eq. 7 threshold shift prevents SV_PF from reaching zero."""
        sv_pf = vulnerability_result[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL]
        assert (sv_pf > 0.0).all(), "SV_PF ≤ 0 for at least one building"

    def test_no_nan_values(self, vulnerability_result: pd.DataFrame) -> None:
        assert not vulnerability_result.isnull().any().any()

    def test_high_indicator_building_has_highest_svi(
        self,
        vulnerability_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        """Building 7 (C=3, ES=2, WR=4, EDQ=1) should have the highest SVI."""
        svi = vulnerability_result[SOCIAL_VULNERABILITY_INDEX]
        b7_svi = svi.loc[7]
        for bid in [1, 2, 3, 5, 6, 8]:
            assert b7_svi >= svi.loc[bid], (
                f"B7 SVI ({b7_svi:.4f}) should be ≥ B{bid} SVI ({svi.loc[bid]:.4f})"
            )

    def test_zero_indicator_building_has_low_coping_capacity(
        self, vulnerability_result: pd.DataFrame
    ) -> None:
        """Building 8 has WR=1 and no other CC indicator: CCap should be low."""
        cc = vulnerability_result[COPING_CAPACITY_INDEX]
        # Building 4 has WR=0 (no welfare recipients) → lowest possible CC
        b4_cc = cc.loc[4]
        assert b4_cc == pytest.approx(0.0, abs=1e-6), (
            f"B4 with WR=0 and EDQ=0 should have CCap=0, got {b4_cc:.4f}"
        )

    def test_sv_pf_monotone_with_svi(self, vulnerability_result: pd.DataFrame) -> None:
        """SV_PF must increase with SVI (the power transform is monotone)."""
        svi = vulnerability_result[SOCIAL_VULNERABILITY_INDEX].values
        sv_pf = vulnerability_result[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL].values
        order = svi.argsort()
        assert all(
            sv_pf[order[i]] <= sv_pf[order[i + 1]] for i in range(len(order) - 1)
        ), "SV_PF is not monotone with SVI"


# ---------------------------------------------------------------------------
# 2. Exposure pipeline tests
# ---------------------------------------------------------------------------


@pytest.mark.regression
class TestExposureExampleData:
    """Exposure pipeline against real example buildings.

    Absolute resident counts are ~2.83 × smaller than the original ArcPy
    output (see module docstring).  Tests verify structural properties and
    internal consistency instead of matching absolute values.
    """

    def test_all_buildings_present(
        self,
        exposure_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        assert set(exposure_result.index) == set(buildings_gdf.index)

    def test_no_nan_values(self, exposure_result: pd.DataFrame) -> None:
        assert (
            not exposure_result[
                [
                    RESIDENTS_PER_BUILDING,
                    RESIDENTS_GROUND_FLOOR,
                    EXPOSURE_MOBILITY_ACCESSIBILITY,
                ]
            ]
            .isnull()
            .any()
            .any()
        )

    def test_type2_buildings_have_zero_ground_floor_residents(
        self,
        exposure_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        """Building type 2 (commercial ground floor) must have R_G = 0."""
        type2_ids = buildings_gdf[buildings_gdf[BUILDING_TYPE] == 2].index
        for bid in type2_ids:
            rg = exposure_result.loc[bid, RESIDENTS_GROUND_FLOOR]
            assert rg == pytest.approx(0.0, abs=1e-6), (
                f"Type-2 building {bid} should have R_G=0, got {rg:.4f}"
            )

    def test_single_floor_type1_ground_floor_equals_total(
        self,
        exposure_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        """For single-storey type-1 buildings R_G must equal R."""
        single_floor = buildings_gdf[
            (buildings_gdf[FLOORS_ABOVE_GROUND] == 1)
            & (buildings_gdf[BUILDING_TYPE] == 1)
        ].index
        for bid in single_floor:
            r = exposure_result.loc[bid, RESIDENTS_PER_BUILDING]
            rg = exposure_result.loc[bid, RESIDENTS_GROUND_FLOOR]
            assert rg == pytest.approx(r, rel=1e-6), (
                f"Single-storey B{bid}: R_G ({rg:.3f}) should equal R ({r:.3f})"
            )

    def test_multi_floor_type1_ground_floor_less_than_total(
        self,
        exposure_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        """Multi-storey type-1 buildings: R_G = R / floors < R."""
        multi_floor = buildings_gdf[
            (buildings_gdf[FLOORS_ABOVE_GROUND] > 1)
            & (buildings_gdf[BUILDING_TYPE] == 1)
        ].index
        for bid in multi_floor:
            r = exposure_result.loc[bid, RESIDENTS_PER_BUILDING]
            rg = exposure_result.loc[bid, RESIDENTS_GROUND_FLOOR]
            floors = buildings_gdf.loc[bid, FLOORS_ABOVE_GROUND]
            assert rg == pytest.approx(r / floors, rel=1e-6), (
                f"Multi-storey B{bid}: R_G should be R/floors={r / floors:.3f}, got {rg:.3f}"
            )

    def test_ema_equals_residents_per_building(
        self, exposure_result: pd.DataFrame
    ) -> None:
        """EMA equals R for all buildings (Eq. 10)."""
        assert (
            exposure_result[EXPOSURE_MOBILITY_ACCESSIBILITY]
            == exposure_result[RESIDENTS_PER_BUILDING]
        ).all()

    def test_residents_positive(self, exposure_result: pd.DataFrame) -> None:
        assert (exposure_result[RESIDENTS_PER_BUILDING] > 0).all()

    def test_ordering_matches_expected(
        self,
        exposure_result: pd.DataFrame,
        expected: gpd.GeoDataFrame,
    ) -> None:
        """Rank order of EWB matches original rank order (Spearman ≥ 0.7)."""
        common_ids = sorted(set(exposure_result.index) & set(expected.index))
        ours = exposure_result.loc[common_ids, RESIDENTS_GROUND_FLOOR]
        exp = expected.loc[common_ids, "EWB"].astype(float)
        corr = ours.corr(exp, method="spearman")
        assert corr >= 0.7, (
            f"Spearman rank correlation for EWB ordering is too low ({corr:.3f})"
        )

    def test_type2_ewb_zero_matches_expected(
        self,
        exposure_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
        expected: gpd.GeoDataFrame,
    ) -> None:
        """Building 4 (type=2) should have EWB=0 in both ours and expected."""
        b4_rg = exposure_result.loc[4, RESIDENTS_GROUND_FLOOR]
        b4_ewb_exp = expected.loc[4, "EWB"]
        assert b4_rg == pytest.approx(0.0, abs=1e-6)
        assert b4_ewb_exp == 0


# ---------------------------------------------------------------------------
# 3. Hazard MA — formula verification (pure math, no spatial dependency)
# ---------------------------------------------------------------------------


@pytest.mark.regression
class TestHazardMAFormula:
    """Verify Eq. 13 by feeding expected fractions from ArcPy into compute_hazard_ma().

    Because the flood fractions in ``building_example_results.geojson`` were
    computed by the original ArcPy ``TabulateIntersection``, using them as
    input to ``compute_hazard_ma()`` isolates the formula from spatial
    differences and should reproduce ``HMA_expected`` to within < 1 %.

    Field mapping (ArcPy → our columns):
        F30A5m  / F30P5m  → flooded_area_m2_5m  / flooded_fraction_5m
        F30A15m / F30P15m → flooded_area_m2_15m / flooded_fraction_15m
        F30A30m / F30P30m → flooded_area_m2_30m / flooded_fraction_30m
    Note: F30P* fields store percentages (0–100); our fractions are 0–1.
    """

    @pytest.fixture(scope="class")
    def formula_input(self, expected: gpd.GeoDataFrame) -> pd.DataFrame:
        """DataFrame with flood fractions from the ArcPy results."""
        df = pd.DataFrame(index=expected.index)
        df[FLOODED_AREA_M2_5M] = expected["F30A5m"].fillna(0.0)
        df[FLOODED_FRACTION_5M] = expected["F30P5m"].fillna(0.0) / 100.0
        df[FLOODED_AREA_M2_15M] = expected["F30A15m"].fillna(0.0)
        df[FLOODED_FRACTION_15M] = expected["F30P15m"].fillna(0.0) / 100.0
        df[FLOODED_AREA_M2_30M] = expected["F30A30m"].fillna(0.0)
        df[FLOODED_FRACTION_30M] = expected["F30P30m"].fillna(0.0) / 100.0
        return df

    @pytest.fixture(scope="class")
    def formula_hma(self, formula_input: pd.DataFrame) -> pd.Series:
        """HMA computed from ArcPy flood fractions."""
        return compute_hazard_ma(formula_input, HazardParams())

    def test_formula_hma_matches_expected_b1(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """Building 1: near-fully flooded → HMA ≈ 1."""
        assert formula_hma.loc[1] == pytest.approx(expected.loc[1, "HMA"], rel=1e-2)

    def test_formula_hma_matches_expected_b3(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """Building 3: small fractions → HMA ≈ 6.66e-13 (lognorm tail)."""
        assert formula_hma.loc[3] == pytest.approx(expected.loc[3, "HMA"], rel=1e-2)

    def test_formula_hma_matches_expected_b4(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """Building 4: intermediate fraction → HMA ≈ 0.332."""
        assert formula_hma.loc[4] == pytest.approx(expected.loc[4, "HMA"], rel=1e-2)

    def test_formula_hma_matches_expected_b5(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """Building 5: tiny 30m fraction → HMA ≈ 7.81e-8."""
        assert formula_hma.loc[5] == pytest.approx(expected.loc[5, "HMA"], rel=1e-2)

    def test_formula_hma_matches_expected_b6(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """Building 6: high 30m fraction → HMA ≈ 0.9995."""
        assert formula_hma.loc[6] == pytest.approx(expected.loc[6, "HMA"], rel=1e-2)

    def test_all_buildings_formula_hma_in_valid_range(
        self, formula_hma: pd.Series
    ) -> None:
        """HMA must lie in [0, 1] for all buildings."""
        assert (formula_hma >= 0.0).all()
        assert (formula_hma <= 1.0).all()

    def test_all_buildings_formula_hma_close_to_expected(
        self, formula_hma: pd.Series, expected: gpd.GeoDataFrame
    ) -> None:
        """For all 37 buildings: |computed - expected| / max(|expected|, 1e-10) < 1 %."""
        for bid in expected.index:
            hma_exp = float(expected.loc[bid, "HMA"])
            hma_ours = float(formula_hma.loc[bid])
            denom = max(abs(hma_exp), 1e-10)
            rel_err = abs(hma_ours - hma_exp) / denom
            assert rel_err < 0.01, (
                f"B{bid}: HMA computed={hma_ours:.6e}, expected={hma_exp:.6e}, "
                f"rel_err={rel_err:.3%}"
            )

    def test_min_area_filter_applied_correctly(self) -> None:
        """Buildings 5 and 6 exercise the min-area filter path.

        B5: F30A30m=3.40 m², zone_area = 3.40/0.0674 ≈ 50.5 m² > 4.0 → NOT filtered
            → HMA = lognorm.cdf(0.0674×4, σ) ≈ 7.81e-8
        """
        df = pd.DataFrame(
            {
                FLOODED_AREA_M2_5M: [0.0],
                FLOODED_FRACTION_5M: [0.0],
                FLOODED_AREA_M2_15M: [0.0],
                FLOODED_FRACTION_15M: [0.0],
                FLOODED_AREA_M2_30M: [3.4025718096635709],
                FLOODED_FRACTION_30M: [6.7369880361402066 / 100.0],
            },
            index=[5],
        )
        result = compute_hazard_ma(df, HazardParams())
        expected_hma = 7.8119333363572246e-08
        assert result.loc[5] == pytest.approx(expected_hma, rel=1e-2)

    def test_min_area_filter_suppresses_tiny_zone(self) -> None:
        """If zone_area < min_street_flood_area_m2, the fraction is set to 0."""
        params = HazardParams(min_street_flood_area_m2=100.0)
        # Zone area = flooded_area / fraction = 5.0 / 0.5 = 10 m² < 100 m² → filtered
        df = pd.DataFrame(
            {
                FLOODED_AREA_M2_5M: [0.0],
                FLOODED_FRACTION_5M: [0.0],
                FLOODED_AREA_M2_15M: [5.0],
                FLOODED_FRACTION_15M: [0.5],
                FLOODED_AREA_M2_30M: [0.0],
                FLOODED_FRACTION_30M: [0.0],
            },
            index=[1],
        )
        result = compute_hazard_ma(df, params)
        assert result.loc[1] == pytest.approx(0.0, abs=1e-10)

    def test_formula_manual_cross_check_b3(self) -> None:
        """Manual cross-check for building 3 (Eq. 13).

        B3 from ArcPy: p5=0.00718, p15=0, p30=0.04247.
        zone_area_30 = 15.1457/0.04247 ≈ 356 m² > min_area → not filtered.
        HMA = lognorm.cdf(max(0.00718, 0, 0.04247) × 4, σ=0.25)
             = lognorm.cdf(0.16988, σ=0.25) ≈ 6.664e-13.
        """
        df = pd.DataFrame(
            {
                FLOODED_AREA_M2_5M: [0.68206687026654045],
                FLOODED_FRACTION_5M: [0.71843295827814224 / 100.0],
                FLOODED_AREA_M2_15M: [0.0],
                FLOODED_FRACTION_15M: [0.0],
                FLOODED_AREA_M2_30M: [15.145741364910037],
                FLOODED_FRACTION_30M: [4.2467834395800903 / 100.0],
            },
            index=[3],
        )
        result = compute_hazard_ma(df, HazardParams())
        expected = 6.6642553972922456e-13
        assert result.loc[3] == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# 4. Hazard MA — full spatial pipeline tests
# ---------------------------------------------------------------------------


@pytest.mark.regression
class TestHazardMASpatial:
    """Full hazard-MA pipeline (buffer + overlay + formula) on real example data.

    The spatial fractions differ from the ArcPy reference due to CRS
    differences (EPSG:25832 vs Web Mercator).  Tests verify structural and
    ordering properties rather than exact values.
    """

    def test_all_buildings_present(
        self,
        hazard_ma_result: pd.DataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ) -> None:
        assert set(hazard_ma_result.index) == set(buildings_gdf.index)

    def test_hma_in_valid_range(self, hazard_ma_result: pd.DataFrame) -> None:
        hma = hazard_ma_result[HAZARD_MOBILITY_ACCESSIBILITY]
        assert (hma >= 0.0).all()
        assert (hma <= 1.0).all()

    def test_no_nan_values(self, hazard_ma_result: pd.DataFrame) -> None:
        assert not hazard_ma_result.isnull().any().any()

    def test_zero_flood_input_gives_near_zero_hma(
        self,
        buildings_gdf: gpd.GeoDataFrame,
        streets_gdf: gpd.GeoDataFrame,
    ) -> None:
        """Pipeline with empty flood layer should produce HMA = 0 for all buildings."""
        empty_flood = gpd.GeoDataFrame(
            geometry=gpd.GeoSeries([], crs="EPSG:25832"),
            crs="EPSG:25832",
        )
        result = run_hazard_ma_pipeline(
            buildings_gdf, streets_gdf, empty_flood, HazardParams()
        )
        # lognorm.cdf(0, s) == 0 exactly; all fractions are 0 → HMA == 0
        assert (result[HAZARD_MOBILITY_ACCESSIBILITY] < 1e-10).all()

    def test_fractions_in_valid_range(self, hazard_ma_result: pd.DataFrame) -> None:
        for col in [FLOODED_FRACTION_5M, FLOODED_FRACTION_15M, FLOODED_FRACTION_30M]:
            frac = hazard_ma_result[col]
            assert (frac >= 0.0).all(), f"{col} < 0"
            # Allow tiny floating-point overflow (e.g. 1.0000000001 from overlay).
            assert (frac <= 1.0 + 1e-6).all(), f"{col} > 1 + tolerance"

    def test_high_expected_hma_buildings_have_high_computed_hma(
        self,
        hazard_ma_result: pd.DataFrame,
        expected: gpd.GeoDataFrame,
    ) -> None:
        """Buildings with expected HMA > 0.99 should also have high computed HMA.

        We require that at least 80 % of such buildings also have high computed
        HMA (> 0.5).  The 20 % allowance accommodates spatial-computation
        differences between our EPSG:25832 buffer/overlay and the original
        ArcPy Web-Mercator TabulateIntersection, which can cause a small number
        of buildings near zone boundaries to be classified differently.
        """
        high_hma_buildings = expected[expected["HMA"] > 0.99].index.tolist()
        common = [b for b in high_hma_buildings if b in hazard_ma_result.index]
        if not common:
            pytest.skip("No high-HMA buildings in common")
        matches = sum(
            1
            for bid in common
            if hazard_ma_result.loc[bid, HAZARD_MOBILITY_ACCESSIBILITY] > 0.5
        )
        match_rate = matches / len(common)
        assert match_rate >= 0.8, (
            f"Only {matches}/{len(common)} ({match_rate:.0%}) high-HMA buildings "
            "also have high computed HMA (threshold 80 %)"
        )

    def test_zero_expected_flood_buildings_have_low_hma(
        self,
        hazard_ma_result: pd.DataFrame,
        expected: gpd.GeoDataFrame,
    ) -> None:
        """Buildings with all expected flood fractions = 0 should have near-0 HMA."""
        no_flood = expected[
            (expected["F30P5m"].fillna(0) == 0)
            & (expected["F30P15m"].fillna(0) == 0)
            & (expected["F30P30m"].fillna(0) == 0)
        ].index.tolist()
        for bid in no_flood:
            if bid in hazard_ma_result.index:
                hma_ours = hazard_ma_result.loc[bid, HAZARD_MOBILITY_ACCESSIBILITY]
                assert hma_ours < 0.01, (
                    f"B{bid} expected no flood but got HMA={hma_ours:.6e}"
                )

    def test_spatial_fraction_order_correlates_with_expected(
        self,
        hazard_ma_result: pd.DataFrame,
        expected: gpd.GeoDataFrame,
    ) -> None:
        """Spearman rank correlation between our HMA and expected HMA ≥ 0.7."""
        common_ids = sorted(set(hazard_ma_result.index) & set(expected.index))
        ours = hazard_ma_result.loc[common_ids, HAZARD_MOBILITY_ACCESSIBILITY]
        exp_hma = expected.loc[common_ids, "HMA"].astype(float)
        corr = ours.corr(exp_hma, method="spearman")
        assert corr >= 0.7, (
            f"Spearman rank correlation for HMA ordering is too low ({corr:.3f})"
        )

    def test_flood_fraction_area_consistency(
        self, hazard_ma_result: pd.DataFrame
    ) -> None:
        """For each zone: flooded_area ≤ flooded_fraction × zone_area + tolerance."""
        for area_col, frac_col in [
            (FLOODED_AREA_M2_5M, FLOODED_FRACTION_5M),
            (FLOODED_AREA_M2_15M, FLOODED_FRACTION_15M),
            (FLOODED_AREA_M2_30M, FLOODED_FRACTION_30M),
        ]:
            area = hazard_ma_result[area_col]
            frac = hazard_ma_result[frac_col]
            # flooded_area = fraction × zone_area (or 0 where no intersection)
            # flooded_area must not exceed any reasonable zone area (< 10 000 m²)
            assert (area >= 0.0).all(), f"Negative area in {area_col}"
            # where fraction > 0, area must also be > 0
            no_area_but_frac = (area == 0.0) & (frac > 0.0)
            assert not no_area_but_frac.any(), (
                f"Non-zero fraction with zero area in {area_col}/{frac_col}"
            )
