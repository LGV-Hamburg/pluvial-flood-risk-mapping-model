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
from pathlib import Path
from typing import cast

import geopandas as gpd
import pandas as pd
from fastprocesses.core.base_process import BaseScatterProcess, parallel_step
from fastprocesses.core.models import (
    ProcessDescription,
    ProcessInput,
    ProcessJobControlOptions,
    ProcessOutput,
    ProcessOutputTransmission,
    Schema,
)
from fastprocesses.processes.process_registry import register_process
from pydantic import BaseModel
from referencing import Registry, Resource

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
    RISK_CLASS_MOBILITY_ACCESSIBILITY,
    RISK_CLASS_WELL_BEING,
    SENSITIVITY_INDEX,
    SINGLE_PARENTS,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
    SOCIAL_WELFARE_RECIPIENTS,
    STATISTICAL_UNIT_ID,
)
from ..models.inputs import PipelineParams, ProcessInputs
from ..spatial_utils import join_statistical_unit_attributes

_INPUT_BUILDING_OUTPUT_COLUMNS = [
    FLOORS_ABOVE_GROUND,
    BUILDING_TYPE,
    CHILDREN_UNDER_10,
    ELDERLY_SINGLES_OVER_65,
    SOCIAL_WELFARE_RECIPIENTS,
    NO_HIGHSCHOOL_DIPLOMA,
    STATISTICAL_UNIT_ID,
    RESIDENTS_IN_STATISTICAL_UNIT,
    LIVING_AREA_M2_STATISTICAL_UNIT,
]

# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class PfRiskResult(BaseModel):
    """Combined output of the pluvial flood risk pipeline.

    Fields mirror the process outputs declared in ``process_description.outputs``.
    """

    # GeoJSON FeatureCollection containing all computed columns for every building
    risk_buildings: dict | None = None
    voronoi_diagram: dict | None = None  # GeoJSON FeatureCollection of Voronoi polygons

    # Head/tail break thresholds for cartographic classification
    classification_breaks_wb: list[float] | None = None  # for pluvial_flood_risk_well_being
    classification_breaks_ma: list[float] | None = None  # for pluvial_flood_risk_mobility_accessibility


    validation_report: dict | None = None



#---------------------------------------------------------------------------
# Helpers
#---------------------------------------------------------------------------

def load_schema_file(path: str) -> Resource:
    schema = json.loads(Path(path).read_text())
    return Resource.from_contents(schema)

# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------


@register_process("pluvial_flood_risk")
class PluvialFloodRiskProcess(BaseScatterProcess):
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
    """
    schema_registry = Registry().with_resources([
        (
            "https://geojson.org/schema/FeatureCollection.json",
            load_schema_file("schemas/geojson-featurecollection.json")
        ),
        (
            "https://geojson.org/schema/Polygon.json",
            load_schema_file("schemas/geojson-Polygon.json")
        ),
        (
            "https://geojson.org/schema/MultiPolygon.json",
            load_schema_file("schemas/geojson-MultiPolygon.json")
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/buildings-schema.json",
            load_schema_file("schemas/buildings-schema.json")
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/streets-schema.json",  
            load_schema_file("schemas/streets-schema.json")
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/statistical-units-schema.json",
            load_schema_file("schemas/statistical-units-schema.json")
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/flood-layers-schema.json",
            load_schema_file("schemas/flood-layers-schema.json")
        ),
    ])

    process_description = ProcessDescription.from_yaml(
        "pluvial_flood_risk_process_description.yaml"
    )
    # ------------------------------------------------------------------
    # CRS helper
    # ------------------------------------------------------------------

    @staticmethod
    def _project_crs_to_metric(
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

    @staticmethod
    def _convert_geojson_to_gdf(
        features: list, source_crs: str | None = None
    ) -> gpd.GeoDataFrame:
        """Parse a GeoJSON features list into a GeoDataFrame.

        Parameters
        ----------
        features:
            GeoJSON feature list from a FeatureCollection.
        source_crs:
            CRS to assign when no CRS is embedded in the features.  Pass
            ``params.target_crs`` whenever the input coordinates are already
            in a projected system (e.g. ``'EPSG:25832'``).  Defaults to
            ``'EPSG:4326'`` for standard WGS-84 GeoJSON.
        """
        gdf = gpd.GeoDataFrame.from_features(features)
        if gdf.crs is None:
            gdf = gdf.set_crs(source_crs or "EPSG:4326")
        return gdf

    @staticmethod
    def _json_scalar(value):
        """Convert pandas / numpy scalars to JSON-safe Python values."""
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            return value.item()
        return value

    @classmethod
    def _serialize_output_value(cls, value, decimal_precision: int | None):
        """Convert output values to JSON-safe scalars with optional rounding."""
        scalar = cls._json_scalar(value)
        if scalar is None or decimal_precision is None:
            return scalar
        if isinstance(scalar, float):
            rounded = round(scalar, decimal_precision)
            return 0.0 if rounded == 0 else rounded
        return scalar

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
        inp = ProcessInputs.from_exec_body(exec_body)
        params = inp
        features: list[dict] = inp.buildings["features"]

        # Check if vulnerability indicators need to be joined from stat units
        vulnerability_cols = [
            CHILDREN_UNDER_10,
            ELDERLY_SINGLES_OVER_65,
            SOCIAL_WELFARE_RECIPIENTS,
            NO_HIGHSCHOOL_DIPLOMA,
        ]
        if params.vulnerability.sensitivity_weights.single_parents > 0:
            vulnerability_cols.append(SINGLE_PARENTS)
        sample_props = features[0]["properties"] if features else {}
        indicators_missing = any(
            col not in sample_props for col in vulnerability_cols
        )

        if indicators_missing and inp.statistical_units is not None:
            # Parse as GeoDataFrame to do the spatial/attribute join
            gdf = self._convert_geojson_to_gdf(features, params.target_crs)
            gdf_metric = self._project_crs_to_metric(gdf, params.target_crs)
            gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

            stat_gdf = self._convert_geojson_to_gdf(
                inp.statistical_units["features"], params.target_crs
            )
            gdf_metric = join_statistical_unit_attributes(gdf_metric, stat_gdf)

            if "id" in gdf_metric.columns:
                gdf_metric = gdf_metric.set_index("id")
            df = pd.DataFrame(gdf_metric.drop(columns="geometry"))
        else:
            rows = [
                {"_id": f.get("id") or f["properties"].get("id"), **f["properties"]}
                for f in features
            ]
            df = pd.DataFrame(rows).set_index("_id")

        result_df = run_vulnerability_pipeline(df, params.vulnerability)

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
        inp = ProcessInputs.from_exec_body(exec_body)
        params = inp

        gdf = self._convert_geojson_to_gdf(
            inp.buildings["features"], params.target_crs
        )
        gdf_metric = self._project_crs_to_metric(gdf, params.target_crs)
        gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

        residents_missing = RESIDENTS_IN_STATISTICAL_UNIT not in gdf_metric.columns
        living_area_missing = LIVING_AREA_M2_STATISTICAL_UNIT not in gdf_metric.columns
        unit_id_missing = STATISTICAL_UNIT_ID not in gdf_metric.columns

        # Join statistical unit attributes when residents are missing, or when
        # living area must be derived but no unit assignment is available.
        needs_join = residents_missing or (living_area_missing and unit_id_missing)
        if needs_join:
            if inp.statistical_units is None:
                raise ValueError(
                    "The buildings FeatureCollection is missing "
                    "'residents_in_statistical_unit', or cannot derive "
                    "'living_area_m2_statistical_unit' because "
                    "'statistical_unit_id' is absent. Provide residents and "
                    "statistical_unit_id as building properties, or supply a "
                    "'statistical_units' FeatureCollection input for automatic "
                    "joining."
                )
            stat_gdf = self._convert_geojson_to_gdf(
                inp.statistical_units["features"], params.target_crs
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

        # Recover the building id used as dict key (keep it in index).
        if "id" in gdf_metric.columns:
            gdf_metric = gdf_metric.set_index("id")

        result_df = run_exposure_pipeline(gdf_metric)

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

        buildings_gdf = self._convert_geojson_to_gdf(
            inp.buildings["features"], params.target_crs
        )
        buildings_metric = self._project_crs_to_metric(buildings_gdf, params.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        flood_gdf = self._convert_geojson_to_gdf(
            inp.flood_layers["features"], params.target_crs
        )
        flood_metric = self._project_crs_to_metric(
            flood_gdf, params.target_crs, buildings_gdf
        )

        result_df = run_hazard_wb_pipeline(
            buildings_metric, flood_metric, params.hazard
        )

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
        inp = ProcessInputs.from_exec_body(exec_body)
        params = inp

        buildings_gdf = self._convert_geojson_to_gdf(
            inp.buildings["features"], params.target_crs
        )
        buildings_metric = self._project_crs_to_metric(buildings_gdf, params.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        streets_gdf = self._convert_geojson_to_gdf(
            inp.streets["features"], params.target_crs
        )
        streets_metric = self._project_crs_to_metric(
            streets_gdf, params.target_crs, buildings_gdf
        )

        # Filter flood features to the configured MA depth class.
        ma_level = params.hazard.ma_flood_level_cm
        ma_features = [
            f
            for f in inp.flood_layers["features"]
            if f.get("properties", {}).get("level_cm") == ma_level
        ]
        if not ma_features:
            available = sorted(
                {
                    f.get("properties", {}).get("level_cm")
                    for f in inp.flood_layers["features"]
                }
            )
            raise ValueError(
                f"No flood features found with level_cm={ma_level} in "
                f"'flood_layers'. Available levels: {available}"
            )
        flood_gdf = self._convert_geojson_to_gdf(ma_features, params.target_crs)
        flood_metric = self._project_crs_to_metric(
            flood_gdf, params.target_crs, buildings_gdf
        )

        result_df = run_hazard_ma_pipeline(
            buildings_metric, streets_metric, flood_metric, params.hazard
        )

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

    def merge_results(self, results: dict[str, dict], exec_body: dict) -> PfRiskResult:
        """Merge partial results, calculate risk indices (Eq. 14/15), classify.

        Args:
            results: Mapping of step name → dict returned by that branch.
            exec_body: The original process execution body.

        Returns:
            RiskResult with GeoJSON FeatureCollection and classification breaks.
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

        inp = ProcessInputs.from_exec_body(exec_body)
        source_features = inp.buildings["features"]
        params = inp

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
        risk_df = run_risk_pipeline(merged_df, params.risk_weights)
        classified: ClassificationResult = run_classification_pipeline(
            risk_df,
            params.n_classification_iterations,
        )

        output_columns = [
            SENSITIVITY_INDEX,
            COPING_CAPACITY_INDEX,
            SOCIAL_VULNERABILITY_INDEX,
            SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
            BUILDING_LIVING_AREA_M2,
            RESIDENTS_PER_BUILDING,
            RESIDENTS_GROUND_FLOOR,
            EXPOSURE_MOBILITY_ACCESSIBILITY,
            HAZARD_WELL_BEING,
            HAZARD_MOBILITY_ACCESSIBILITY,
            PLUVIAL_FLOOD_RISK_WELL_BEING,
            PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
            RISK_CLASS_WELL_BEING,
            RISK_CLASS_MOBILITY_ACCESSIBILITY,
        ]

        # Decide which outputs the client requested (OGC API Processes)
        want_risk_buildings = self._is_output_requested(exec_body, "risk_buildings")
        want_voronoi = self._is_output_requested(exec_body, "voronoi_diagram")
        want_class_breaks_wb = self._is_output_requested(exec_body, "classification_breaks_wb")
        want_class_breaks_ma = self._is_output_requested(exec_body, "classification_breaks_ma")

        # Candidate columns for aggregation (used by HEALPix and unit aggregation)
        candidate_cols = self._candidate_agg_columns()

        # Build the enriched buildings GeoDataFrame when any output that depends
        # on it (risk_buildings, voronoi_diagram) is requested.
        enriched_features = None
        source_buildings_gdf = None
        if want_risk_buildings or want_voronoi:
            enriched_features, source_buildings_gdf = self._build_enriched_features_and_bld_gdf(
                source_features, classified, params, output_columns, candidate_cols
            )

        risk_buildings = {
            "type": "FeatureCollection", "features": enriched_features or []
        } if want_risk_buildings else None

        # Voronoi / Thiessen tessellation (optional output)
        voronoi_diagram_fc = None
        if want_voronoi and source_buildings_gdf is not None:
            blds_metric = self._project_crs_to_metric(
                source_buildings_gdf, params.target_crs
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
                    self._serialize_output_value(value, params.output.decimal_precision),
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
                    self._serialize_output_value(value, params.output.decimal_precision),
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
    def _is_output_requested(self, exec_body: dict, name: str) -> bool:
        """Return True when the given output should be produced by this run.

        Follows OGC behaviour: if no `outputs` object is present in the
        execution body, all outputs are produced by default.
        """
        outputs = exec_body.get("outputs")
        if not outputs:
            return True
        if isinstance(outputs, dict):
            return name in outputs
        return True

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

    def _build_enriched_features_and_bld_gdf(
        self,
        source_features: list[dict],
        classified: ClassificationResult,
        params: PipelineParams,
        output_columns: list[str],
        candidate_cols: list[str],
    ) -> tuple[list[dict], gpd.GeoDataFrame]:
        """Return (enriched_features, buildings_gdf) for downstream outputs.

        Attaches numeric columns from `classified.data` to the GeoDataFrame
        and serialises the building features according to `params.output`.
        """
        blds = self._convert_geojson_to_gdf(source_features, params.target_crs)
        if "id" in blds.columns:
            blds = blds.set_index("id")
        blds.index = blds.index.astype(str)

        classified_df = classified.data.copy()
        classified_df.index = classified_df.index.astype(str)
        for col in candidate_cols:
            if col in classified_df.columns:
                blds[col] = classified_df[col].reindex(blds.index).values

        enriched_features = []
        for feature in source_features:
            feature_id = feature.get("id") or feature.get("properties", {}).get("id")
            if feature_id is None:
                raise ValueError("Each building feature must provide an id either at the feature top level or in properties.id.")
            building_id = str(feature_id)
            if building_id not in classified.data.index:
                raise ValueError(f"Missing merged results for building id {building_id!r}.")
            source_properties = feature.get("properties", {})
            if params.output.include_input_building_properties:
                properties = {
                    column: source_properties[column]
                    for column in _INPUT_BUILDING_OUTPUT_COLUMNS
                    if column in source_properties
                }
            else:
                properties = {}
            row = classified.data.loc[building_id]
            for column in output_columns:
                properties[column] = self._serialize_output_value(row[column], params.output.decimal_precision)
            enriched_features.append({**feature, "properties": properties})

        return enriched_features, blds
