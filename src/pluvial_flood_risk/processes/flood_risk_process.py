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

from ..algorithms.exposure import run_exposure_pipeline
from ..algorithms.vulnerability import run_vulnerability_pipeline
from ..models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_LIVING_AREA_M2,
    COPING_CAPACITY_INDEX,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_IN_STATISTICAL_UNIT,
    RESIDENTS_PER_BUILDING,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
    STATISTICAL_UNIT_ID,
)
from ..models.inputs import PipelineParams

# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class RiskResult(BaseModel):
    """Combined output of the pluvial flood risk pipeline."""

    # GeoJSON FeatureCollection containing all computed columns for every building
    risk_features: dict

    # Head/tail break thresholds for cartographic classification
    classification_breaks_wb: list[float]  # for pluvial_flood_risk_well_being
    classification_breaks_ma: list[
        float
    ]  # for pluvial_flood_risk_mobility_accessibility


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

    process_description = ProcessDescription(
        id="pluvial_flood_risk",
        title="Pluvial Flood Risk",
        version="1.0.0",
        description=(
            "Calculates building-level pluvial flood risk indices "
            "(PFR_WB, PFR_MA) following the IPCC risk framework "
            "(Vogelbacher et al., 2025): "
            "Risk = Hazard^a x Exposure^b x Vulnerability^c. "
            "The four sub-pipelines run in parallel via a scatter-gather pattern."
        ),
        jobControlOptions=[
            ProcessJobControlOptions.ASYNC_EXECUTE,
        ],
        outputTransmission=[ProcessOutputTransmission.VALUE],
        inputs={
            "buildings": ProcessInput(
                title="Buildings Feature Collection",
                description=(
                    "GeoJSON FeatureCollection of building footprints. "
                    "Each feature must carry all socio-economic indicator "
                    "properties as well as the population lookup fields "
                    "(statistical_unit_id, residents_in_statistical_unit, "
                    "living_area_m2_statistical_unit)."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                        {
                            "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/buildings-schema.json"
                        },
                    ]
                ),
                minOccurs=1,
                maxOccurs=1,
            ),
            "streets": ProcessInput(
                title="Streets Feature Collection",
                description=(
                    "GeoJSON FeatureCollection of street centre-line geometries. "
                    "Used in the mobility & accessibility hazard calculation (Step 3) "
                    "to construct the 15 m and 30 m buffer zones around road access."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                        {
                            "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/streets-schema.json"
                        },
                    ]
                ),
                minOccurs=1,
                maxOccurs=1,
            ),
            "statistical_units": ProcessInput(
                title="Statistical Units Feature Collection (optional)",
                description=(
                    "GeoJSON FeatureCollection of statistical unit polygons carrying "
                    "census attributes. Required only when 'residents_in_statistical_unit' "
                    "and 'living_area_m2_statistical_unit' are NOT pre-joined onto the "
                    "building features. Each feature must have properties: "
                    "'statistical_unit_id' (matching the building property), "
                    "'residents' (total registered residents), and "
                    "'living_area_m2' (total residential floor area [m²]). "
                    "The join is performed by attribute (statistical_unit_id), "
                    "not spatially."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                        {
                            "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/statistical-units-schema.json"
                        },
                    ]
                ),
                minOccurs=0,
                maxOccurs=1,
            ),
            "flood_layers_wb": ProcessInput(
                title="Flood Layers — Well-Being (multiple depth classes)",
                description=(
                    "Array of GeoJSON FeatureCollections representing flooded areas, "
                    "one entry per water-depth class (typically 20–100 cm in 10 cm "
                    "increments, i.e. up to 9 layers). Array order must be ascending "
                    "depth. Used for the 2 m building buffer hazard calculation "
                    "(Steps 4.1–4.2, Eq. 11/12)."
                ),
                schema=Schema(
                    type="array",
                    minItems=1,
                    maxItems=9,
                    items={
                        "allOf": [
                            {"format": "geojson-feature-collection"},
                            {
                                "$ref": "https://geojson.org/schema/FeatureCollection.json"
                            },
                            {
                                "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/flood-layer-wb-schema.json"
                            },
                        ]
                    },
                ),
                minOccurs=1,
                maxOccurs=1,
            ),
            "flood_layer_ma": ProcessInput(
                title="Flood Layer — Mobility & Accessibility (30 cm scenario)",
                description=(
                    "GeoJSON FeatureCollection of flooded areas for the 30 cm water "
                    "depth scenario. Used to compute the hazard index for mobility & "
                    "accessibility via concentric buffer zones (Steps 3.1–3.2, Eq. 13)."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                        {
                            "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/flood-layer-ma-schema.json"
                        },
                    ]
                ),
                minOccurs=1,
                maxOccurs=1,
            ),
            "params": ProcessInput(
                title="Pipeline Parameters (optional)",
                description=(
                    "JSON object for overriding algorithm defaults. "
                    "All fields are optional; omitting this input applies the "
                    "paper-derived defaults (Vogelbacher et al., 2025)."
                ),
                schema=Schema(
                    type="object",
                    properties={
                        "vulnerability_weights": Schema.model_validate(
                            {
                                "type": "object",
                                "properties": {
                                    "children_under_10": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.3,
                                    },
                                    "elderly_singles_over_65": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.7,
                                    },
                                    "social_welfare_recipients": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.5,
                                    },
                                    "no_highschool_diploma": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.5,
                                    },
                                    "sensitivity": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.5,
                                    },
                                    "coping_capacity": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.5,
                                    },
                                    "apply_entropy": {
                                        "type": "boolean",
                                        "default": True,
                                        "description": "Apply Shannon entropy weighting to TOPSIS scores",
                                    },
                                },
                            }
                        ),
                        "flood_sensitivity_params": Schema.model_validate(
                            {
                                "type": "object",
                                "properties": {
                                    "threshold_fraction": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "maximum": 1.0,
                                        "default": 0.25,
                                        "description": "Fraction of mean SVI added as floor value (Eq. 7)",
                                    },
                                    "exponent": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "default": 2,
                                        "maximum": 3,
                                        "description": "Power applied to shifted SVI (Eq. 7)",
                                    },
                                },
                            }
                        ),
                        "hazard_params": Schema.model_validate(
                            {
                                "type": "object",
                                "properties": {
                                    "buffer_building_m": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 2.0,
                                        "description": "Buffer radius around buildings for HWB [m]",
                                    },
                                    "buffer_street_inner_m": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 15.0,
                                        "description": "Inner street buffer radius for HMA [m]",
                                    },
                                    "buffer_street_outer_m": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 30.0,
                                        "description": "Outer street buffer radius for HMA [m]",
                                    },
                                    "buffer_access_m": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 5.0,
                                        "description": "Direct access buffer radius for HMA [m]",
                                    },
                                    "min_street_flood_area_m2": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 4.0,
                                        "description": "Minimum flooded street area to include in HMA [m²]",
                                    },
                                    "lognormal_shape": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 0.25,
                                        "description": "Shape parameter σ of the log-normal CDF (Eq. 11)",
                                    },
                                },
                            }
                        ),
                        "risk_weights": Schema.model_validate(
                            {
                                "type": "object",
                                "properties": {
                                    "hazard_exponent": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 1.0,
                                        "description": "Exponent a applied to hazard index (Eq. 14/15)",
                                    },
                                    "exposure_exponent": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 1.0,
                                        "description": "Exponent b applied to exposure index (Eq. 14/15)",
                                    },
                                    "vulnerability_exponent": {
                                        "type": "number",
                                        "minimum": 0.0,
                                        "default": 1.0,
                                        "description": "Exponent c applied to vulnerability index (Eq. 14/15)",
                                    },
                                    "n_classification_iterations": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "default": 4,
                                        "description": "Number of head/tail break iterations for risk classification",
                                    },
                                },
                            }
                        ),
                    },
                ),
                minOccurs=0,
                maxOccurs=1,
            ),
        },
        outputs={
            "risk_features": ProcessOutput(
                title="Risk Features",
                description=(
                    "GeoJSON FeatureCollection of building footprints enriched with "
                    "all computed columns: sensitivity_index, coping_capacity_index, "
                    "social_vulnerability_index, social_vulnerability_flood_potential, "
                    "residents_per_building, residents_ground_floor, "
                    "hazard_well_being, hazard_mobility_accessibility, "
                    "pluvial_flood_risk_well_being, "
                    "pluvial_flood_risk_mobility_accessibility, "
                    "risk_class_wb, risk_class_ma."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                    ]
                ),
            ),
            "classification_breaks_wb": ProcessOutput(
                title="Classification Breaks — Well-Being",
                description=(
                    "Head/tail break thresholds for pluvial_flood_risk_well_being. "
                    "Use as class boundaries for cartographic visualisation."
                ),
                schema=Schema(type="array", items={"type": "number"}),
            ),
            "classification_breaks_ma": ProcessOutput(
                title="Classification Breaks — Mobility & Accessibility",
                description=(
                    "Head/tail break thresholds for "
                    "pluvial_flood_risk_mobility_accessibility."
                ),
                schema=Schema(type="array", items={"type": "number"}),
            ),
        },
        keywords=[
            "flood",
            "risk",
            "pluvial",
            "IPCC",
            "vulnerability",
            "hazard",
            "urban",
        ],
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

        Returns:
            dict with keys: sensitivity_index, coping_capacity_index,
            social_vulnerability_index, social_vulnerability_flood_potential
            (one value per building, keyed by building id).
        """
        inputs: dict = exec_body["inputs"]
        buildings_fc: dict = inputs["buildings"]
        params = PipelineParams.model_validate(inputs.get("params", {}))

        features: list[dict] = buildings_fc["features"]
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
        inputs: dict = exec_body["inputs"]
        buildings_fc: dict = inputs["buildings"]

        # Parse GeoJSON → GeoDataFrame so we can derive footprint area from geometry.
        gdf = gpd.GeoDataFrame.from_features(buildings_fc["features"])
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        # Project to a metric CRS for accurate area computation.
        gdf_metric = gdf.to_crs(gdf.estimate_utm_crs())
        gdf_metric[BUILDING_FOOTPRINT_AREA_M2] = gdf_metric.geometry.area

        # Join residents / living_area from statistical_units when not pre-joined.
        needs_join = (
            RESIDENTS_IN_STATISTICAL_UNIT not in gdf_metric.columns
            or LIVING_AREA_M2_STATISTICAL_UNIT not in gdf_metric.columns
        )
        if needs_join:
            stat_units_fc = inputs.get("statistical_units")
            if stat_units_fc is None:
                raise ValueError(
                    "The buildings FeatureCollection is missing "
                    "'residents_in_statistical_unit' and/or "
                    "'living_area_m2_statistical_unit'. "
                    "Provide these as building properties, or supply a "
                    "'statistical_units' FeatureCollection input so the "
                    "values can be joined automatically."
                )
            stat_rows = [
                {
                    STATISTICAL_UNIT_ID: f["properties"]["statistical_unit_id"],
                    RESIDENTS_IN_STATISTICAL_UNIT: f["properties"]["residents"],
                    LIVING_AREA_M2_STATISTICAL_UNIT: f["properties"]["living_area_m2"],
                }
                for f in stat_units_fc["features"]
            ]
            stat_df = pd.DataFrame(stat_rows).drop_duplicates(
                subset=STATISTICAL_UNIT_ID
            )
            gdf_metric = gdf_metric.merge(stat_df, on=STATISTICAL_UNIT_ID, how="left")

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

        Returns:
            dict with key: hazard_well_being
            (one value per building, keyed by building id).
        """
        raise NotImplementedError

    @parallel_step
    def hazard_ma_pipeline(self, exec_body: dict) -> dict:
        """Steps 3.1-3.2: Concentric buffer flood intersection + log-normal CDF → HMA.

        Returns:
            dict with key: hazard_mobility_accessibility
            (one value per building, keyed by building id).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Gather / merge
    #
    # Called once all parallel steps have finished.
    # results = {step_name: result_dict}
    # ------------------------------------------------------------------

    def merge_results(self, results: dict[str, dict]) -> RiskResult:
        """Merge partial results, calculate risk indices (Eq. 14/15), classify.

        Args:
            results: Mapping of step name → dict returned by that branch.

        Returns:
            RiskResult with GeoJSON FeatureCollection and classification breaks.
        """
        raise NotImplementedError
