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

from ..algorithms.vulnerability import run_vulnerability_pipeline
from ..models.field_names import (
    COPING_CAPACITY_INDEX,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
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
                            "type": "object",
                            "properties": {
                                "features": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["type", "geometry", "properties"],
                                        "properties": {
                                            "type": {"type": "string"},
                                            "geometry": {
                                                "$ref": "https://geojson.org/schema/Polygon.json"
                                            },
                                            "properties": {
                                                "type": "object",
                                                "required": [
                                                    "id",
                                                    "floors_above_ground",
                                                    "building_type",
                                                    "children_under_10",
                                                    "elderly_singles_over_65",
                                                    "social_welfare_recipients",
                                                    "no_highschool_diploma",
                                                    "statistical_unit_id",
                                                    "residents_in_statistical_unit",
                                                    "living_area_m2_statistical_unit",
                                                ],
                                                "properties": {
                                                    "id": {
                                                        "type": ["integer", "string"]
                                                    },
                                                    "floors_above_ground": {
                                                        "type": "integer",
                                                        "minimum": 1,
                                                        "description": "Number of floors above ground",
                                                    },
                                                    "building_type": {
                                                        "type": "integer",
                                                        "minimum": 1,
                                                        "description": "Building type code (1 = detached, 2 = semi-detached, …)",
                                                    },
                                                    "children_under_10": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Number of children under 10 years (sensitivity indicator C)",
                                                    },
                                                    "elderly_singles_over_65": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Number of elderly singles over 65 years (sensitivity indicator ES)",
                                                    },
                                                    "social_welfare_recipients": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Number of social welfare recipients (coping capacity indicator WR)",
                                                    },
                                                    "no_highschool_diploma": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Number of residents without high-school diploma in last 3 years (coping capacity indicator EDQ)",
                                                    },
                                                    "statistical_unit_id": {
                                                        "type": ["integer", "string"],
                                                        "description": "Reference key linking building to a statistical unit",
                                                    },
                                                    "residents_in_statistical_unit": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Total residents registered in the statistical unit",
                                                    },
                                                    "living_area_m2_statistical_unit": {
                                                        "type": "number",
                                                        "minimum": 0,
                                                        "description": "Total residential floor area [m²] of the statistical unit",
                                                    },
                                                },
                                            },
                                        },
                                    },
                                }
                            },
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
                            "type": "object",
                            "properties": {
                                "features": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["type", "geometry"],
                                        "properties": {
                                            "type": {"type": "string"},
                                            "geometry": {
                                                "$ref": "https://geojson.org/schema/LineString.json"
                                            },
                                        },
                                    },
                                }
                            },
                        },
                    ]
                ),
                minOccurs=1,
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
                                "type": "object",
                                "required": ["depth_cm"],
                                "properties": {
                                    "depth_cm": {
                                        "type": "integer",
                                        "minimum": 20,
                                        "maximum": 100,
                                        "multipleOf": 10,
                                        "description": "Water depth class this layer represents [cm]",
                                    }
                                },
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
            dict with keys: residents_per_building, residents_ground_floor
            (one value per building, keyed by building id).
        """
        raise NotImplementedError

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
