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
import os
from pathlib import Path
from typing import ClassVar, cast

import geopandas as gpd
import pandas as pd
from fastprocesses import BaseProcessResult
from fastprocesses.core.base_process import BaseScatterProcess, parallel_step
from fastprocesses.core.models import ProcessDescription
from fastprocesses.processes.process_registry import register_process
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
from ..io import fetch_all_references
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
from ..models.inputs import (
    BuildingsFeatureCollection,
    FloodLayersFeatureCollection,
    PipelineParams,
    ProcessInputs,
    ProcessOutputs,
    StatisticalUnitsFeatureCollection,
    StreetsFeatureCollection,
)
from ..spatial_utils import join_statistical_unit_attributes

logger = logging.getLogger(__name__)

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


class PfRiskResult(BaseProcessResult):
    """Combined output of the pluvial flood risk pipeline.

    Fields mirror the process outputs declared in ``process_description.outputs``.
    """

    output_serializers: ClassVar = {
            "risk_buildings": {
                "application/geo+json": "risk_buildings_to_json",
            },
            "voronoi_diagram": {
                "application/geo+json": "voronoi_diagram_to_json",
            },
            "classification_breaks_wb": {
                "application/json": "classification_breaks_wb_to_json"
            },
            "classification_breaks_ma": {
                "application/json": "classification_breaks_ma_to_json"
            }
        }

    def risk_buildings_to_json(self) -> bytes:
            return self.model_dump_json().encode()
    
    def voronoi_diagram_to_json(self) -> bytes:
            return self.model_dump_json().encode()
    
    def classification_breaks_wb_to_json(self) -> bytes:
            return self.model_dump_json().encode()
    
    def classification_breaks_ma_to_json(self) -> bytes:
            return self.model_dump_json().encode()

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
    # Remote input resolution (Phase 1 hook)
    # ------------------------------------------------------------------

    _REFERENCE_INPUTS = ("buildings", "streets", "flood_layers")

    def resolve_remote_inputs(self, exec_body: dict) -> dict:
        """Download any URL-valued inputs and replace them with resolved data.

        Detects ``{href, type}`` reference objects in ``buildings``,
        ``streets``, and ``flood_layers``.  Inline FeatureCollection dicts
        are passed through unchanged.

        The SSRF allowlist is read from the ``FP_ALLOWED_INPUT_ORIGINS``
        environment variable.  Its value must be a JSON array of origin
        prefixes (e.g. ``'["https://trusted.example.com/"]'``) or a
        comma-separated list of prefixes.  The variable **must** be set
        when any URL input is present; an unset or empty value raises
        ``ValueError``.

        Supported media types (``type`` field of the reference object):

        - ``application/geo+json`` — fetched via OGC API Features paging
          (:func:`~pluvial_flood_risk.io.fetch_ogc_features`).
        - ``application/flatgeobuf`` — fetched as raw bytes and decoded via
          :func:`~pluvial_flood_risk.io.fetch_flatgeobuf`.
        """
        inputs = dict(exec_body["inputs"])
        references = [
            key for key in self._REFERENCE_INPUTS
            if isinstance(inputs.get(key), dict) and "href" in inputs[key]
        ]
        if not references:
            logger.debug("resolve_remote_inputs: all inputs are inline, skipping")
            return exec_body

        logger.info("resolve_remote_inputs: downloading remote inputs: %s", references)
        allowed_origins = self._load_allowed_origins()

        ref_map = {key: inputs[key] for key in references}
        resolved = fetch_all_references(ref_map, allowed_origins=allowed_origins)
        for key, data in resolved.items():
            logger.info(
                "Resolved '%s': %d features",
                key, len(data.get("features", [])),
            )
            inputs[key] = data

        return {**exec_body, "inputs": inputs}

    @staticmethod
    def _load_allowed_origins() -> list[str]:
        """Read FP_ALLOWED_INPUT_ORIGINS from the environment.

        Raises
        ------
        ValueError
            If the variable is missing or empty.
        """
        raw = os.environ.get("FP_ALLOWED_INPUT_ORIGINS", "").strip()
        if not raw:
            raise ValueError(
                "FP_ALLOWED_INPUT_ORIGINS is not set. "
                "Configure an allowlist of trusted origin prefixes "
                "(JSON array or comma-separated) before accepting URL inputs."
            )
        try:
            origins = json.loads(raw)
            if not isinstance(origins, list):
                raise ValueError("FP_ALLOWED_INPUT_ORIGINS must be a JSON array.")
            return origins
        except json.JSONDecodeError:
            return [o.strip() for o in raw.split(",") if o.strip()]

    # ------------------------------------------------------------------
    # Validation hook
    # ------------------------------------------------------------------

    def late_validate(self, inputs: dict) -> bool:
        """Validate resolved FeatureCollections against typed geojson-pydantic models.

        Called by fastprocesses after ``resolve_remote_inputs`` substitutes any
        URL-valued inputs with downloaded data.  At this point every spatial input
        is a plain GeoJSON dict and can be parsed against its typed alias.

        Raises ``ValueError`` (forwarded to the job as a user-facing error) if any
        FeatureCollection is structurally invalid or a feature is missing required
        properties (e.g. ``depth_cm`` on flood layers, ``residents`` on statistical
        units).  Extra properties are silently ignored (``extra='ignore'``).
        """
        logger.info("late_validate: validating resolved FeatureCollections")
        BuildingsFeatureCollection.model_validate(inputs["buildings"])
        logger.debug("late_validate: buildings OK (%d features)", len(inputs["buildings"].get("features", [])))
        StreetsFeatureCollection.model_validate(inputs["streets"])
        logger.debug("late_validate: streets OK (%d features)", len(inputs["streets"].get("features", [])))
        FloodLayersFeatureCollection.model_validate(inputs["flood_layers"])
        logger.debug("late_validate: flood_layers OK (%d features)", len(inputs["flood_layers"].get("features", [])))
        if inputs.get("statistical_units") is not None:
            StatisticalUnitsFeatureCollection.model_validate(inputs["statistical_units"])
            logger.debug(
                "late_validate: statistical_units OK (%d features)",
                len(inputs["statistical_units"].get("features", [])),
            )
        logger.info("late_validate: all inputs valid")
        return True

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
            gdf = self._convert_geojson_to_gdf(buildings_features, input.target_crs)
            gdf_metric = self._project_crs_to_metric(gdf, input.target_crs)
            gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

            stat_gdf = self._convert_geojson_to_gdf(
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
        result_df = run_vulnerability_pipeline(df, input.vulnerability_parameters)
        logger.debug("vulnerability_pipeline: done")

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

        gdf = self._convert_geojson_to_gdf(
            input.buildings["features"], input.target_crs
        )
        gdf_metric = self._project_crs_to_metric(gdf, input.target_crs)
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
            stat_gdf = self._convert_geojson_to_gdf(
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

        buildings_gdf = self._convert_geojson_to_gdf(
            input.buildings["features"], input.target_crs
        )
        buildings_metric = self._project_crs_to_metric(buildings_gdf, input.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        streets_gdf = self._convert_geojson_to_gdf(
            input.streets["features"], input.target_crs
        )
        streets_metric = self._project_crs_to_metric(
            streets_gdf, input.target_crs, buildings_gdf
        )

        # Filter flood features to the configured MA depth class.
        ma_level = input.hazard_parameters.ma_flood_level_cm
        ma_features = [
            f
            for f in input.flood_layers["features"]
            if f.get("properties", {}).get("level_cm") == ma_level
        ]
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
        
        flood_gdf = self._convert_geojson_to_gdf(ma_features, input.target_crs)
        
        flood_metric = self._project_crs_to_metric(
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

    def merge_results(self, results: dict[str, dict], exec_body: dict) -> PfRiskResult:
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
        
        output: ProcessOutputs | None = ProcessOutputs.model_validate(
            exec_body.get("outputs", None)
        )

        buildings_features = input.buildings["features"]

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
        want_risk_buildings = self._is_output_requested(output, "risk_buildings")
        want_voronoi = self._is_output_requested(output, "voronoi_diagram")
        want_class_breaks_wb = self._is_output_requested(
            output, "classification_breaks_wb"
        )
        want_class_breaks_ma = self._is_output_requested(
            output, "classification_breaks_ma"
        )

        # Candidate columns for aggregation (used by HEALPix and unit aggregation)
        candidate_cols = self._candidate_agg_columns()

        # Build the enriched buildings GeoDataFrame when any output that depends
        # on it (risk_buildings, voronoi_diagram) is requested.
        enriched_features = None
        source_buildings_gdf = None
        if want_risk_buildings or want_voronoi:
            enriched_features, source_buildings_gdf = (
                self._build_enriched_features_and_bld_gdf(
                    buildings_features, classified, input,
                    output_columns, candidate_cols
                )
            )

        risk_buildings = {
            "type": "FeatureCollection", "features": enriched_features or []
        } if want_risk_buildings else None

        # Voronoi / Thiessen tessellation (optional output)
        voronoi_diagram_fc = None
        if want_voronoi and source_buildings_gdf is not None:
            blds_metric = self._project_crs_to_metric(
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
                    self._serialize_output_value(
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
                    self._serialize_output_value(
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
    def _is_output_requested(self, outputs: ProcessOutputs | None, name: str) -> bool:
        """Return True when the given output should be produced by this run.

        Follows OGC behaviour: if no `outputs` object is present in the
        execution body, all outputs are produced by default.
        """
        if outputs is None:
            return True

        return getattr(outputs, name) is not None

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
        input: PipelineParams,
        output_columns: list[str],
        candidate_cols: list[str],
    ) -> tuple[list[dict], gpd.GeoDataFrame]:
        """Return (enriched_features, buildings_gdf) for downstream outputs.

        Attaches numeric columns from `classified.data` to the GeoDataFrame
        and serialises the building features according to `inputs.output_settings`.
        """
        buildings_gdf = self._convert_geojson_to_gdf(source_features, input.target_crs)

        if "id" in buildings_gdf.columns:
            buildings_gdf = buildings_gdf.set_index("id")
        buildings_gdf.index = buildings_gdf.index.astype(str)

        classified_df = classified.data.copy()
        classified_df.index = classified_df.index.astype(str)
        
        for col in candidate_cols:
            if col in classified_df.columns:
                buildings_gdf[col] = classified_df[col].reindex(
                    buildings_gdf.index
                ).values

        enriched_features = []
        for feature in source_features:
            feature_id = feature.get("id") or feature.get("properties", {}).get("id")
            if feature_id is None:
                raise ValueError(
                    "Each building feature must provide an " \
                    "id either at the feature top level or in properties.id."
                )
            building_id = str(feature_id)
            if building_id not in classified.data.index:
                raise ValueError(
                    f"Missing merged results for building id {building_id!r}."
                )
            
            source_properties = feature.get("properties", {})
            
            if input.output_settings.include_input_building_properties:
                properties = {
                    column: source_properties[column]
                    for column in _INPUT_BUILDING_OUTPUT_COLUMNS
                    if column in source_properties
                }
            else:
                properties = {}
            row = classified.data.loc[building_id]
            for column in output_columns:
                properties[column] = self._serialize_output_value(
                    row[column], input.output_settings.decimal_precision
                )
            enriched_features.append({**feature, "properties": properties})

        return enriched_features, buildings_gdf
