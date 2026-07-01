"""OGC API Process: Pluvial Flood Risk Calculation.

Implements the IPCC risk framework from Vogelbacher et al. (2025):

    Risk = Hazard^a x Exposure^b x Vulnerability^c

Two parallel risk indices are produced per building:
    PFR_WB  — Pluvial Flood Risk to Well-Being
    PFR_MA  — Pluvial Flood Risk to Mobility & Accessibility

Execution pattern (BaseScatterProcess / operation fan-out):
    vulnerability_pipeline  ─┐
    exposure_pipeline        ┤
    hazard_wb_pipeline       ┤  →  merge_results  →  RiskResult
    hazard_ma_pipeline      ─┘

Each branch receives the full exec_body and runs on its own Celery worker.
"""

import json
import logging
import time
from typing import cast

import pandas as pd
from fastprocesses.core.base_process import BaseScatterProcess, parallel_step
from fastprocesses.core.models import ProcessDescription
from fastprocesses.core.types import JobProgressCallback
from fastprocesses.processes.process_registry import register_process

from ..algorithms.aggregations import create_thiessen_polygons
from ..algorithms.classification import (
    ClassificationResult,
    run_classification_pipeline,
)
from ..algorithms.exposure import run_exposure_pipeline
from ..algorithms.hazard_ma import run_hazard_ma_pipeline
from ..algorithms.hazard_wb import run_hazard_wb_pipeline
from ..algorithms.risk import run_risk_pipeline
from ..algorithms.vulnerability import run_vulnerability_pipeline
from ..models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_LIVING_AREA_M2,
    BUILDING_TYPE,
    CHILDREN_UNDER_10,
    COPING_CAPACITY_INDEX,
    ELDERLY_SINGLES_OVER_65,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    FLOORS_ABOVE_GROUND,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    NO_HIGHSCHOOL_DIPLOMA,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_IN_STATISTICAL_UNIT,
    RESIDENTS_PER_BUILDING,
    SENSITIVITY_INDEX,
    SINGLE_PARENTS,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
    SOCIAL_WELFARE_RECIPIENTS,
    STATISTICAL_UNIT_ID,
)
from ..models.inputs import ProcessInputs
from ..serialization import build_enriched_features, serialize_output_value
from ..settings import settings
from ..spatial_utils import (
    convert_geojson_to_gdf,
    expand_boundary_by_meters,
    filter_features_to_boundary,
    filter_gdf_to_boundary,
    join_statistical_unit_attributes,
    project_crs_to_metric,
)
from ._pfr_base import (
    _HAZARD_BUFFER_HALO_M,
    _INPUT_BUILDING_OUTPUT_COLUMNS,
    _OUTPUT_COLUMNS,
    BasePfRProcess,
    PfRiskResult,
)

logger = logging.getLogger(__name__)


def _get_boundary_buildings(
    input: "ProcessInputs",
    step_name: str,
) -> list[dict]:
    """Return building features from the local subset, filtered to calculation_boundary.

    Reads ``buildings_local`` when available (pre-filtered to intersecting
    statistical units), falling back to the full ``buildings`` collection.
    When ``calculation_boundary`` is set, applies an additional exact-boundary
    filter and logs the result.
    """
    features: list[dict] = (input.buildings_local or input.buildings)["features"]
    if input.calculation_boundary is not None:
        _total = len(features)
        features = filter_features_to_boundary(features, input.calculation_boundary)
        logger.info(
            "%s: calculation_boundary applied — %d/%d buildings in boundary",
            step_name, len(features), _total,
        )
    return features


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------


@register_process("pluvial-flood-risk-regional")
class PluvialFloodRiskProcess(BasePfRProcess, BaseScatterProcess):
    """Pluvial flood risk at building level following Vogelbacher et al. (2025).

    Four independent computation branches run in parallel (scatter-gather):

    vulnerability_pipeline  — Steps 1.1-1.4
        TOPSIS-based sensitivity + coping capacity → SVI → flood sensitivity transform

    exposure_pipeline       — Step 2
        Residents per building and ground-floor exposure index (EWB)

    hazard_wb_pipeline      — Steps 4.1-4.2
        2 m buffer x flood-depth scenarios → log-normal CDF → HWB

    hazard_ma_pipeline      — Steps 3.1-3.2
        Concentric street buffers (5 / 15 / 30 m) x flood layer → log-normal CDF → HMA

    Scope rules when ``calculation_boundary`` is set:
        - vulnerability is GLOBAL — TOPSIS normalises against the full city
          population, so per-building V is unaffected by the boundary.
        - exposure / hazard are LOCAL — computed only for boundary buildings
          (exposure keeps the full statistical-unit population for the
          living-area sum, then filters). Per-building results are exact.
        - merge risk is exact; classification breaks are REGIONAL (quantiles
          over the boundary subset), not city-wide.
    """

    process_description = ProcessDescription.from_yaml(
        str(settings.process_description_pfr_file)
    )

    # ------------------------------------------------------------------
    # Parallel branches
    #
    # Each @parallel_step runs on its own Celery worker and receives the
    # full exec_body.  Return only the newly computed columns as a plain
    # dict so they can be serialised and passed back from the worker.
    # ------------------------------------------------------------------

    @parallel_step
    def vulnerability_pipeline(self, exec_body: dict) -> dict:
        """Steps 1.1-1.4: Sensitivity → Coping Capacity → SVI → SV_PF.

        When vulnerability indicators are missing from building features but a
        ``statistical_units`` input is provided, they are joined from the
        statistical units (by attribute or spatially).

        Returns:
            dict with keys: sensitivity_index, coping_capacity_index,
            social_vulnerability_index, social_vulnerability_flood_potential
            (one value per building, keyed by building id).
        """
        input = ProcessInputs.from_exec_body(exec_body)

        buildings_features: list[dict] = input.buildings["features"]

        # Check if vulnerability indicators need to be joined from stat units
        vulnerability_cols = [
            CHILDREN_UNDER_10,
            ELDERLY_SINGLES_OVER_65,
            SOCIAL_WELFARE_RECIPIENTS,
            NO_HIGHSCHOOL_DIPLOMA,
        ]
        if input.vulnerability_parameters.sensitivity_weights.single_parents > 0:
            vulnerability_cols.append(SINGLE_PARENTS)

        sample_props = buildings_features[0]["properties"] if buildings_features else {}
        indicators_missing = any(
            col not in sample_props for col in vulnerability_cols
        )

        if indicators_missing and input.statistical_units is not None:
            # Parse as GeoDataFrame to do the spatial/attribute join
            gdf = convert_geojson_to_gdf(buildings_features, input.target_crs)
            gdf_metric = project_crs_to_metric(gdf, input.target_crs)
            gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

            stat_gdf = convert_geojson_to_gdf(
                input.statistical_units["features"], input.target_crs
            )
            gdf_metric = join_statistical_unit_attributes(gdf_metric, stat_gdf)

            if "id" in gdf_metric.columns:
                gdf_metric = gdf_metric.set_index("id")
            df = pd.DataFrame(gdf_metric.drop(columns="geometry"))
        else:
            rows = [
                {"_id": f.get("id") or f["properties"].get("id"), **f["properties"]}
                for f in buildings_features
            ]
            df = pd.DataFrame(rows).set_index("_id")

        logger.info("vulnerability_pipeline: %d buildings", len(df))
        _t_vuln = time.perf_counter()
        result_df = run_vulnerability_pipeline(df, input.vulnerability_parameters)
        logger.info(
            "vulnerability_pipeline: done in %.2fs",
            time.perf_counter() - _t_vuln,
        )

        output_columns = [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        ]
        return {
            col: result_df[col].where(result_df[col].notna(), other=None).to_dict()
            for col in output_columns
        }

    @parallel_step
    def exposure_pipeline(self, exec_body: dict) -> dict:
        """Step 2: Residents per building and ground-floor exposure index.

        Returns:
            dict with keys: building_living_area_m2, residents_per_building,
            residents_ground_floor, exposure_mobility_accessibility
            (one value per building, keyed by building id).
        """
        input = ProcessInputs.from_exec_body(exec_body)

        # Build the GDF from ALL buildings first — unit-level aggregations
        # (living_area_m2_statistical_unit groupby) must span the full unit
        # population.  The boundary filter is applied after those aggregations.
        # buildings_local already covers whole units intersecting the boundary,
        # so unit-level sums remain correct while avoiding the full city set.
        gdf = convert_geojson_to_gdf(
            (input.buildings_local or input.buildings)["features"],
            input.target_crs,
        )
        gdf_metric = project_crs_to_metric(gdf, input.target_crs)
        gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

        residents_missing = RESIDENTS_IN_STATISTICAL_UNIT not in gdf_metric.columns
        living_area_missing = LIVING_AREA_M2_STATISTICAL_UNIT not in gdf_metric.columns
        unit_id_missing = STATISTICAL_UNIT_ID not in gdf_metric.columns

        # Join statistical unit attributes when residents are missing, or when
        # living area must be derived but no unit assignment is available.
        needs_join = residents_missing or (living_area_missing and unit_id_missing)

        if needs_join:
            if input.statistical_units is None:
                raise ValueError(
                    "The buildings FeatureCollection is missing "
                    "'residents_in_statistical_unit', or cannot derive "
                    "'living_area_m2_statistical_unit' because "
                    "'statistical_unit_id' is absent. Provide residents and "
                    "statistical_unit_id as building properties, or supply a "
                    "'statistical_units' FeatureCollection input for automatic "
                    "joining."
                )
            stat_gdf = convert_geojson_to_gdf(
                input.statistical_units["features"], input.target_crs
            )
            gdf_metric = join_statistical_unit_attributes(gdf_metric, stat_gdf)

        if living_area_missing and STATISTICAL_UNIT_ID in gdf_metric.columns:
            building_living_area = (
                (gdf_metric[FLOORS_ABOVE_GROUND] - (gdf_metric[BUILDING_TYPE] - 1))
                * gdf_metric[BUILDING_FOOTPRINT_AREA_M2]
            )
            gdf_metric[LIVING_AREA_M2_STATISTICAL_UNIT] = building_living_area.groupby(
                gdf_metric[STATISTICAL_UNIT_ID]
            ).transform("sum")

        # Apply the boundary filter now — after unit-level aggregations so that
        # the groupby sum above is not underestimated for boundary-straddling units.
        if input.calculation_boundary is not None:
            _original = len(gdf_metric)
            gdf_metric = filter_gdf_to_boundary(
                gdf_metric, input.calculation_boundary
            )
            logger.info(
                "exposure_pipeline: calculation_boundary applied "
                "— %d/%d buildings in boundary",
                len(gdf_metric), _original,
            )

        # Recover the building id used as dict key (keep it in index).
        if "id" in gdf_metric.columns:
            gdf_metric = gdf_metric.set_index("id")

        logger.info("exposure_pipeline: %d buildings", len(gdf_metric))
        result_df = run_exposure_pipeline(gdf_metric)
        logger.debug("exposure_pipeline: done")

        output_columns = [
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
        ]
        return {
            col: result_df[col].where(result_df[col].notna(), other=None).to_dict()
            for col in output_columns
        }

    @parallel_step
    def hazard_wb_pipeline(self, exec_body: dict) -> dict:
        """Steps 4.1-4.2: 2 m buffer flood intersection + log-normal CDF → HWB.

        Reads buildings and the combined flood_layers input (all depth classes).
        Passes all features to ``run_hazard_wb_pipeline`` which loops over each
        ``level_cm`` depth class to compute per-building flooded fractions.

        Returns:
            dict with key: hazard_well_being
            (one value per building, keyed by building id).
        """
        inp = ProcessInputs.from_exec_body(exec_body)
        params = inp

        _halo_boundary: dict | None = (
            expand_boundary_by_meters(inp.calculation_boundary, _HAZARD_BUFFER_HALO_M)
            if inp.calculation_boundary is not None
            else None
        )

        buildings_features = _get_boundary_buildings(inp, "hazard_wb_pipeline")

        buildings_gdf = convert_geojson_to_gdf(
            buildings_features, params.target_crs
        )
        buildings_metric = project_crs_to_metric(buildings_gdf, params.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        flood_features = inp.flood_layers["features"]
        if _halo_boundary is not None:
            _original = len(flood_features)
            flood_features = filter_features_to_boundary(
                flood_features, _halo_boundary, params.target_crs
            )
            logger.info(
                "hazard_wb_pipeline: boundary +%.0fm halo applied to flood_layers "
                "— %d/%d features in region",
                _HAZARD_BUFFER_HALO_M, len(flood_features), _original,
            )

        flood_gdf = convert_geojson_to_gdf(flood_features, params.target_crs)
        flood_metric = project_crs_to_metric(
            flood_gdf, params.target_crs, buildings_gdf
        )

        logger.info(
            "hazard_wb_pipeline: %d buildings, %d flood features",
            len(buildings_metric), len(flood_gdf),
        )
        result_df = run_hazard_wb_pipeline(
            buildings_metric, flood_metric, params.hazard_parameters
        )
        logger.debug("hazard_wb_pipeline: done")

        return {
            HAZARD_WELL_BEING: result_df[HAZARD_WELL_BEING]
            .where(result_df[HAZARD_WELL_BEING].notna(), other=None)
            .to_dict()
        }

    @parallel_step
    def hazard_ma_pipeline(self, exec_body: dict) -> dict:
        """Steps 3.1-3.2: Concentric buffer flood intersection + log-normal CDF → HMA.

        Reads buildings, streets, and the combined flood_layers input.  Filters
        flood features to those matching ``hazard_params.ma_flood_level_cm``
        (default 30 cm), then calls ``run_hazard_ma_pipeline``.

        Returns:
            dict with key: hazard_mobility_accessibility
            (one value per building, keyed by building id).
        """
        input = ProcessInputs.from_exec_body(exec_body)

        _halo_boundary: dict | None = (
            expand_boundary_by_meters(
                input.calculation_boundary, _HAZARD_BUFFER_HALO_M
            )
            if input.calculation_boundary is not None
            else None
        )

        buildings_features = _get_boundary_buildings(input, "hazard_ma_pipeline")

        buildings_gdf = convert_geojson_to_gdf(
            buildings_features, input.target_crs
        )
        buildings_metric = project_crs_to_metric(buildings_gdf, input.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        streets_features = input.streets["features"]
        if _halo_boundary is not None:
            _original = len(streets_features)
            streets_features = filter_features_to_boundary(
                streets_features, _halo_boundary, input.target_crs
            )
            logger.info(
                "hazard_ma_pipeline: boundary +%.0fm halo applied to streets "
                "— %d/%d features in region",
                _HAZARD_BUFFER_HALO_M, len(streets_features), _original,
            )

        streets_gdf = convert_geojson_to_gdf(streets_features, input.target_crs)
        streets_metric = project_crs_to_metric(
            streets_gdf, input.target_crs, buildings_gdf
        )

        # Filter flood features to the configured MA depth class.
        ma_level = input.hazard_parameters.ma_flood_level_cm
        ma_features = [
            f
            for f in input.flood_layers["features"]
            if f.get("properties", {}).get("level_cm") == ma_level
        ]
        if _halo_boundary is not None:
            _original = len(ma_features)
            ma_features = filter_features_to_boundary(
                ma_features, _halo_boundary, input.target_crs
            )
            logger.info(
                "hazard_ma_pipeline: boundary +%.0fm halo applied to flood_layers "
                "— %d/%d features in region (level_cm=%d)",
                _HAZARD_BUFFER_HALO_M, len(ma_features), _original, ma_level,
            )
        logger.debug(
            "hazard_ma_pipeline: filtering flood_layers to level_cm=%d", ma_level
        )
        if not ma_features:
            available = sorted(
                {
                    f.get("properties", {}).get("level_cm")
                    for f in input.flood_layers["features"]
                }
            )
            raise ValueError(
                f"No flood features found with level_cm={ma_level} in "
                f"'flood_layers'. Available levels: {available}"
            )
        
        flood_gdf = convert_geojson_to_gdf(ma_features, input.target_crs)
        flood_metric = project_crs_to_metric(
            flood_gdf, input.target_crs, buildings_gdf
        )

        logger.info(
            "hazard_ma_pipeline: %d buildings, %d street features, %d flood features (level_cm=%d)",
            len(buildings_metric), len(streets_gdf), len(ma_features), ma_level,
        )
        result_df = run_hazard_ma_pipeline(
            buildings_metric, streets_metric, flood_metric, input.hazard_parameters
        )
        logger.debug("hazard_ma_pipeline: done")

        return {
            HAZARD_MOBILITY_ACCESSIBILITY: result_df[HAZARD_MOBILITY_ACCESSIBILITY]
            .where(result_df[HAZARD_MOBILITY_ACCESSIBILITY].notna(), other=None)
            .to_dict()
        }

    # ------------------------------------------------------------------
    # Gather / merge
    #
    # Called once all parallel steps have finished.
    # results = {step_name: result_dict}
    # ------------------------------------------------------------------

    def merge_results(
            self, results: dict[str, dict], exec_body: dict,
            job_progress_callback: JobProgressCallback | None = None
        ) -> PfRiskResult:
        """Merge partial results, calculate risk indices (Eq. 14/15), classify.

        Args:
            results: Mapping of step name → dict returned by that branch.
            exec_body: The original process execution body.

        Returns:
            PfRiskResult with GeoJSON FeatureCollection and classification breaks.
        """
        required_steps = {
            "vulnerability_pipeline",
            "exposure_pipeline",
            "hazard_wb_pipeline",
            "hazard_ma_pipeline",
        }
        missing_steps = sorted(required_steps - set(results))
        if missing_steps:
            raise ValueError(
                f"merge_results is missing required step outputs: {missing_steps}"
            )

        input = ProcessInputs.from_exec_body(exec_body)

        # None means "outputs" was absent → produce all outputs (OGC req. 27)
        requested = exec_body.get("outputs")

        buildings_features = (input.buildings_local or input.buildings).get(
            "features"
        )

        _boundary_ids: set[str] | None = None
        if input.calculation_boundary is not None and buildings_features is not None:
            original_count = len(buildings_features)
            buildings_features = filter_features_to_boundary(
                buildings_features, input.calculation_boundary
            )
            _boundary_ids = {
                str(f.get("id") or f["properties"].get("id"))
                for f in buildings_features
            }
            logger.info(
                "merge_results: calculation_boundary applied "
                "— %d/%d buildings in boundary",
                len(buildings_features), original_count,
            )

        partial_frames: list[pd.DataFrame] = []
        for step_name in required_steps:
            step_result = results[step_name]
            step_columns = {
                column: pd.Series(
                    data=values.values(),
                    index=[str(building_id) for building_id in values.keys()],
                )
                for column, values in step_result.items()
            }
            partial_frames.append(pd.DataFrame(step_columns))

        merged_df = pd.concat(partial_frames, axis=1)

        # Ensure index is string dtype for consistent lookups
        merged_df.index = merged_df.index.astype(str)

        # Restrict to boundary buildings so risk, classification, and output
        # only cover the requested sub-region.  Per-building risk is exact
        # (Risk = H^a x E^b x V^c has no cross-building term, and V is global),
        # but the head/tail classification breaks are quantiles over this
        # subset — i.e. REGIONAL, not city-wide, when calculation_boundary is set.
        if _boundary_ids is not None:
            merged_df = merged_df[merged_df.index.isin(_boundary_ids)]

        logger.info("merge_results: merging results for %d buildings", len(merged_df))
        risk_df = run_risk_pipeline(merged_df, input.risk_weights)
        logger.debug("merge_results: risk scores computed")
        classified: ClassificationResult = run_classification_pipeline(
            risk_df,
            input.n_classification_iterations,
        )
        logger.info(
            "merge_results: classification done (%d iterations)",
            input.n_classification_iterations,
        )

        output_columns = list(_OUTPUT_COLUMNS)

        # Decide which outputs the client requested (OGC API Processes req. 27:
        # absent "outputs" key → produce all outputs)
        want_risk_buildings = requested is None or "risk_buildings" in requested
        want_voronoi = requested is None or "voronoi_diagram" in requested
        want_class_breaks_wb = (
            requested is None or "classification_breaks_wb" in requested
        )
        want_class_breaks_ma = (
            requested is None or "classification_breaks_ma" in requested
        )

        # Candidate columns for aggregation (used by HEALPix and unit aggregation)
        candidate_cols = self._candidate_agg_columns()

        # Build the enriched buildings GeoDataFrame when any output that depends
        # on it (risk_buildings, voronoi_diagram) is requested.
        enriched_features = None
        source_buildings_gdf = None
        if want_risk_buildings or want_voronoi:
            if buildings_features is not None:
                enriched_features, source_buildings_gdf = build_enriched_features(
                    buildings_features, classified, input,
                    output_columns, candidate_cols,
                    _INPUT_BUILDING_OUTPUT_COLUMNS,
                )
            else:
                logger.warning(
                    "merge_results: buildings geometry unavailable — "
                    "risk_buildings and voronoi_diagram will be empty"
                )

        risk_buildings = {
            "type": "FeatureCollection", "features": enriched_features or []
        } if want_risk_buildings else None

        # Voronoi / Thiessen tessellation (optional output)
        voronoi_diagram_fc = None
        if want_voronoi and source_buildings_gdf is not None:
            blds_metric = project_crs_to_metric(
                source_buildings_gdf, input.target_crs
            )
            thiessen_gdf = create_thiessen_polygons(blds_metric)
            # Reproject back to the original source CRS for GeoJSON output
            source_crs = source_buildings_gdf.crs or "EPSG:4326"
            voronoi_diagram_fc = json.loads(thiessen_gdf.to_crs(source_crs).to_json())

        # Classification breaks (only when requested)
        classification_breaks_wb = (
            [
                cast(
                    float,
                    serialize_output_value(
                        value, input.output_settings.decimal_precision
                    ),
                )
                for value in classified.breaks_well_being
            ]
            if want_class_breaks_wb
            else None
        )
        classification_breaks_ma = (
            [
                cast(
                    float,
                    serialize_output_value(
                        value, input.output_settings.decimal_precision
                    ),
                )
                for value in classified.breaks_mobility_accessibility
            ]
            if want_class_breaks_ma
            else None
        )

        return PfRiskResult(
            risk_buildings=risk_buildings,
            voronoi_diagram=voronoi_diagram_fc,
            classification_breaks_wb=classification_breaks_wb,
            classification_breaks_ma=classification_breaks_ma,
            validation_report=None,
        )

    # ------------------------- Refactor helpers -------------------------
    def _candidate_agg_columns(self) -> list:
        """List of candidate columns used for aggregations."""
        return [
            PLUVIAL_FLOOD_RISK_WELL_BEING,
            PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
            HAZARD_WELL_BEING,
            HAZARD_MOBILITY_ACCESSIBILITY,
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
        ]


