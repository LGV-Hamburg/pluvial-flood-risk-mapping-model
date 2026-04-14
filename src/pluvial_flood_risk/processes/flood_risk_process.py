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
from ..algorithms.hazard_ma import run_hazard_ma_pipeline
from ..algorithms.hazard_wb import run_hazard_wb_pipeline
from ..algorithms.vulnerability import run_vulnerability_pipeline
from ..models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_LIVING_AREA_M2,
    COPING_CAPACITY_INDEX,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
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
            "flood_layers": ProcessInput(
                title="Flood Layers — All Depth Classes",
                description=(
                    "Combined GeoJSON FeatureCollection containing flooded-area "
                    "polygons for all water-depth classes. Each feature must carry "
                    "a 'level_cm' property (integer, typically 20–100 cm in 10 cm "
                    "increments). "
                    "The well-being hazard pipeline (Steps 4.1–4.2) groups features "
                    "by 'level_cm' and processes each depth class independently. "
                    "The mobility & accessibility hazard pipeline (Steps 3.1–3.2) "
                    "uses only features matching "
                    "'hazard_params.ma_flood_level_cm' (default: 30 cm)."
                ),
                schema=Schema(
                    allOf=[
                        {"format": "geojson-feature-collection"},
                        {"$ref": "https://geojson.org/schema/FeatureCollection.json"},
                        {
                            "$ref": "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping/-/raw/main/schemas/flood-layers-schema.json"
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
                                    "ma_flood_level_cm": {
                                        "type": "integer",
                                        "minimum": 20,
                                        "maximum": 100,
                                        "multipleOf": 10,
                                        "default": 30,
                                        "description": "Water depth class [cm] from flood_layers used for the mobility & accessibility hazard (Steps 3.1-3.2)",
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
                                    "target_crs": {
                                        "type": "string",
                                        "default": None,
                                        "examples": [
                                            "EPSG:25832",
                                            "EPSG:32632",
                                            "EPSG:2263",
                                        ],
                                        "description": (
                                            "EPSG code of the projected (metric) CRS for spatial "
                                            "calculations. Omit to auto-detect UTM zone from the "
                                            "input data centroid. Required when inputs are already "
                                            "in a local projected CRS (e.g. 'EPSG:25832' for "
                                            "Hamburg / ETRS89 UTM-32N)."
                                        ),
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
    # CRS helper
    # ------------------------------------------------------------------

    @staticmethod
    def _to_metric(
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
        params = PipelineParams.model_validate(inputs.get("params", {}))

        # Parse GeoJSON → GeoDataFrame so we can derive footprint area from geometry.
        gdf = gpd.GeoDataFrame.from_features(buildings_fc["features"])
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        # Project to a metric CRS for accurate area computation.
        gdf_metric = self._to_metric(gdf, params.target_crs)
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

        Reads buildings and the combined flood_layers input (all depth classes).
        Passes all features to ``run_hazard_wb_pipeline`` which loops over each
        ``level_cm`` depth class to compute per-building flooded fractions.

        Returns:
            dict with key: hazard_well_being
            (one value per building, keyed by building id).
        """
        inputs: dict = exec_body["inputs"]
        buildings_fc: dict = inputs["buildings"]
        flood_layers_fc: dict = inputs["flood_layers"]
        params = PipelineParams.model_validate(inputs.get("params", {}))

        # Parse buildings → GeoDataFrame, project to metric CRS.
        buildings_gdf = gpd.GeoDataFrame.from_features(buildings_fc["features"])
        if buildings_gdf.crs is None:
            buildings_gdf = buildings_gdf.set_crs("EPSG:4326")
        buildings_metric = self._to_metric(buildings_gdf, params.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        # Parse all flood depth classes → same projected CRS.
        flood_gdf = gpd.GeoDataFrame.from_features(flood_layers_fc["features"])
        if flood_gdf.crs is None:
            flood_gdf = flood_gdf.set_crs("EPSG:4326")
        flood_metric = self._to_metric(flood_gdf, params.target_crs, buildings_gdf)

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
        inputs: dict = exec_body["inputs"]
        buildings_fc: dict = inputs["buildings"]
        streets_fc: dict = inputs["streets"]
        flood_layers_fc: dict = inputs["flood_layers"]
        params = PipelineParams.model_validate(inputs.get("params", {}))

        # Parse buildings → GeoDataFrame, project to metric CRS.
        buildings_gdf = gpd.GeoDataFrame.from_features(buildings_fc["features"])
        if buildings_gdf.crs is None:
            buildings_gdf = buildings_gdf.set_crs("EPSG:4326")
        buildings_metric = self._to_metric(buildings_gdf, params.target_crs)
        if "id" in buildings_metric.columns:
            buildings_metric = buildings_metric.set_index("id")

        # Parse streets → same projected CRS (derive from buildings when auto).
        streets_gdf = gpd.GeoDataFrame.from_features(streets_fc["features"])
        if streets_gdf.crs is None:
            streets_gdf = streets_gdf.set_crs("EPSG:4326")
        streets_metric = self._to_metric(streets_gdf, params.target_crs, buildings_gdf)

        # Filter flood features to the configured MA depth class.
        ma_level = params.hazard.ma_flood_level_cm
        ma_features = [
            f
            for f in flood_layers_fc["features"]
            if f.get("properties", {}).get("level_cm") == ma_level
        ]
        if not ma_features:
            available = sorted(
                {
                    f.get("properties", {}).get("level_cm")
                    for f in flood_layers_fc["features"]
                }
            )
            raise ValueError(
                f"No flood features found with level_cm={ma_level} in "
                f"'flood_layers'. Available levels: {available}"
            )
        flood_gdf = gpd.GeoDataFrame.from_features(ma_features)
        if flood_gdf.crs is None:
            flood_gdf = flood_gdf.set_crs("EPSG:4326")
        flood_metric = self._to_metric(flood_gdf, params.target_crs, buildings_gdf)

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

    def merge_results(self, results: dict[str, dict]) -> RiskResult:
        """Merge partial results, calculate risk indices (Eq. 14/15), classify.

        Args:
            results: Mapping of step name → dict returned by that branch.

        Returns:
            RiskResult with GeoJSON FeatureCollection and classification breaks.
        """
        raise NotImplementedError
