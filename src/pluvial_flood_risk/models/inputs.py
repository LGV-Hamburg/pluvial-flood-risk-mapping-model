"""Pydantic parameter models for the pluvial flood risk pipeline.

All classes carry paper-derived defaults from Vogelbacher et al. (2025).
Fields are fully optional at construction — override only what differs from
the paper.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geojson_pydantic import Feature, FeatureCollection
from geojson_pydantic.geometries import GeometryCollection, MultiPolygon, Polygon
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS

# ---------------------------------------------------------------------------
# Vulnerability (Steps 1.1-1.4)
# ---------------------------------------------------------------------------


class SensitivityWeights(BaseModel):
    """Attribute weights for the sensitivity TOPSIS step (Step 1.1).

    Defaults from Vogelbacher et al. (2025), Table / line 155:
        ES (elderly singles) = 0.7, C (children) = 0.3.

    Optionally include single_parents (SP).  When SP weight > 0 the user
    must adjust the other weights so all three sum to 1.0.
    """

    children_under_10: float = Field(default=0.3, ge=0.0, le=1.0)
    elderly_singles_over_65: float = Field(default=0.7, ge=0.0, le=1.0)
    single_parents: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for single-parent households in sensitivity TOPSIS. "
            "Default 0.0 excludes this indicator (paper baseline). "
            "Set > 0 and adjust other weights to include it."
        ),
    )

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> SensitivityWeights:
        total = (
            self.children_under_10
            + self.elderly_singles_over_65
            + self.single_parents
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"SensitivityWeights must sum to 1.0, got {total:.6f}")
        return self


class CopingCapacityWeights(BaseModel):
    """Attribute weights for the coping capacity TOPSIS step (Step 1.2).

    Defaults from Vogelbacher et al. (2025):
        WR (welfare recipients) = 0.5, EDQ (no diploma) = 0.5.
    """

    social_welfare_recipients: float = Field(default=0.5, ge=0.0, le=1.0)
    no_highschool_diploma: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> CopingCapacityWeights:
        total = self.social_welfare_recipients + self.no_highschool_diploma
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"CopingCapacityWeights must sum to 1.0, got {total:.6f}")
        return self


class SVIWeights(BaseModel):
    """Attribute weights for the social vulnerability index TOPSIS step (Step 1.3).

    Defaults from Vogelbacher et al. (2025):
        Sensitivity = 0.5, CopingCapacity = 0.5.
    """

    sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    coping_capacity: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> SVIWeights:
        total = self.sensitivity + self.coping_capacity
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"SVIWeights must sum to 1.0, got {total:.6f}")
        return self


class VulnerabilityParams(BaseModel):
    """All parameters controlling the vulnerability pipeline (Steps 1.1-1.4)."""

    sensitivity_weights: SensitivityWeights = Field(default_factory=SensitivityWeights)
    coping_capacity_weights: CopingCapacityWeights = Field(
        default_factory=CopingCapacityWeights
    )
    svi_weights: SVIWeights = Field(default_factory=SVIWeights)
    apply_entropy: bool = Field(
        default=True,
        description=(
            "Multiply TOPSIS closeness by the Shannon entropy index U_i ∈ [1, 2]. "
            "Accounts for heterogeneity of indicator values per building."
        ),
    )
    threshold_fraction: float = Field(
        default=0.25,
        ge=0.0,
        description=(
            "Fraction of mean(SVI) added as a floor before the power transform "
            "(Eq. 7): SV_PF = (SV + threshold_fraction * mean(SV))^exponent. "
            "Prevents zero-risk when SVI = 0."
        ),
    )
    exponent: float = Field(
        default=2.0,
        gt=0.0,
        description=(
            "Power applied in the flood sensitivity transform (Eq. 7). "
            "Higher values amplify differences between low and high SVI buildings."
        ),
    )


# ---------------------------------------------------------------------------
# Hazard (Steps 3 / 4)
# ---------------------------------------------------------------------------


class HazardParams(BaseModel):
    """Spatial and statistical parameters for the hazard calculations (Steps 3-4)."""

    buffer_building_m: float = Field(
        default=2.0,
        gt=0.0,
        description=(
            "Buffer radius [m] around building footprint "
            "for the well-being hazard (Step 4.1)."
        ),
    )
    buffer_zone1_m: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "Inner buffer radius [m] around building for mobility hazard (Step 3.1)."
        ),
    )
    buffer_zone2_m: float = Field(
        default=15.0,
        gt=0.0,
        description=(
            "Street buffer radius [m] for the near-access zone (Step 3.1)."
        ),
    )
    buffer_zone3_m: float = Field(
        default=30.0,
        gt=0.0,
        description=(
            "Street buffer outer radius [m] for the extended-access zone (Step 3.1)."
        ),
    )
    min_street_flood_area_m2: float = Field(
        default=4.0,
        ge=0.0,
        description=(
            "Minimum flooded road area [m²] to count in the mobility hazard. "
            "Smaller intersections are ignored (Eq. 13 note)."
        ),
    )
    lognormal_shape: float = Field(
        default=0.25,
        gt=0.0,
        description=(
            "Shape parameter σ of the log-normal CDF used for hazard conversion "
            "(Eq. 11). μ is fixed at 0 in the paper."
        ),
    )
    ma_flood_level_cm: int = Field(
        default=30,
        ge=20,
        le=100,
        multiple_of=10,
        description=(
            "Water depth class [cm] selected from the combined flood_layers input "
            "for the mobility & accessibility hazard (Steps 3.1–3.2). "
            "Default: 30 cm as used in Vogelbacher et al. (2025)."
        ),
    )


# ---------------------------------------------------------------------------
# Risk weights (Step 5)
# ---------------------------------------------------------------------------


class RiskWeights(BaseModel):
    """Exponent weights for the IPCC risk product formula (Step 5, Eq. 14 and 15).

    PFR = SV_PF^a * E^b * H^c (this is the general formula for both PFR_WB and PFR_MA).

    All default to 1.0 (unweighted product) as in Vogelbacher et al. (2025).
    """

    vulnerability_exponent: float = Field(
        default=1.0, gt=0.0, description="Exponent a applied to SV_PF."
    )
    exposure_exponent: float = Field(
        default=1.0, gt=0.0, description="Exponent b applied to E (EWB or EMA)."
    )
    hazard_exponent: float = Field(
        default=1.0, gt=0.0, description="Exponent c applied to H (HWB or HMA)."
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


class OutputParams(BaseModel):
    """Controls for the serialized GeoJSON output payload."""

    include_input_building_properties: bool = Field(
        default=True,
        description=(
            "Include selected original building input properties in the output "
            "feature properties in addition to computed pipeline outputs."
        ),
    )
    decimal_precision: int | None = Field(
        default=4,
        ge=0,
        description=(
            "Number of decimal places used for serialized floating-point output "
            "values. Set to null to disable rounding."
        ),
    )
    # (HEALPix parameters moved to a dedicated `HealpixParams` model)


class HealpixParams(BaseModel):
    """Parameters controlling HEALPix aggregation of building-level results."""

    depth: int = Field(
        default=15,
        ge=1,
        le=29,
        description=(
            "HEALPix depth (nside = 2^depth). Higher values produce finer cells."
        ),
    )
    agg_func: str = Field(
        default="mean",
        description=(
            "Aggregation function applied to risk columns "
            "('mean','max','sum','median')."
        ),
    )
    min_buildings: int = Field(
        default=3,
        ge=1,
        description=(
            "Minimum number of buildings per HEALPix "
            "cell required for inclusion."
        ),
    )
    columns: list[str] | None = Field(
        default=None,
        description=(
            "Explicit list of columns to aggregate. Omit "
            "(None) to auto-detect available risk/hazard columns."
        ),
    )



# ---------------------------------------------------------------------------
# Top-level pipeline parameters
# ---------------------------------------------------------------------------


class PipelineParams(BaseModel):
    """Aggregated parameters for the full pluvial flood risk pipeline.

    Construct without arguments to use all paper-derived defaults.

    Example
    -------
    >>> params = PipelineParams()  # all defaults
    >>> params = PipelineParams(vulnerability=VulnerabilityParams(apply_entropy=False))
    """

    vulnerability_parameters: VulnerabilityParams = Field(
        default_factory=VulnerabilityParams
    )
    hazard_parameters: HazardParams = Field(default_factory=HazardParams)
    risk_weights: RiskWeights = Field(default_factory=RiskWeights)
    output_settings: OutputParams = Field(default_factory=OutputParams)
    healpix_params: HealpixParams = Field(default_factory=HealpixParams)
    n_classification_iterations: int = Field(
        default=4,
        ge=1,
        description=(
            "Number of iterations for the head/tail-breaks classification (Step 6). "
            "Yields n+1 risk classes (no risk … very high risk)."
        ),
    )
    target_crs: str | None = Field(
        default=None,
        description=(
            "CRS identifier of the projected (metric) CRS to use for all "
            "spatial calculations (buffer, overlay, area). "
            "Accepted forms include EPSG tokens (e.g. 'EPSG:25832'), "
            "OGC URIs (e.g. 'http://www.opengis.net/def/crs/EPSG/0/25832'), "
            "OGC URNs (e.g. 'urn:ogc:def:crs:EPSG::25832'), and proj strings. "
            "When set, every input layer is reprojected to this CRS regardless of "
            "its original CRS. "
            "When None (default): if the input data is already in a projected CRS "
            "it is used as-is; if the input is geographic (degrees, e.g. WGS84 / "
            "EPSG:4326) the UTM zone is auto-detected via geopandas.estimate_utm_crs(). "
            "Must be a metric (linear unit = metres) CRS."
        ),
    )

    @field_validator("target_crs")
    @classmethod
    def validate_target_crs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("target_crs must not be an empty string")
        try:
            CRS.from_user_input(value)
        except Exception as exc:
            raise ValueError(
                f"Invalid target_crs identifier: {value!r}"
            ) from exc
        return value


# ---------------------------------------------------------------------------
# Geodata property models (for structured validation)
# ---------------------------------------------------------------------------


class BuildingProperties(BaseModel):
    """Building feature properties schema.

    Core required attributes for pluvial flood risk scoring. Additional
    properties are silently ignored to support multipurpose data.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | str = Field(
        description="Unique building identifier (numerical or string)."
    )
    floors_above_ground: int = Field(
        ge=0,
        description="Number of floors above ground (0 = ground floor only).",
    )
    building_type: int | None = Field(
        ge=0,
        description=(
            "Building type (1 = fully residential, 2 = non-residential ground floor, "
            "0 = other)."
        ),
    )


class StreetProperties(BaseModel):
    """Street feature properties schema.

    Core attributes for mobility/accessibility hazard calculations.
    Additional properties are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")



class FloodLayerProperties(BaseModel):
    """Flood layer (depth grid) feature properties schema.

    Represents flood scenarios at specific depth thresholds.
    Additional properties are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")

    level_cm: int = Field(
        ge=0,
        description="Flood water depth in centimeters (e.g., 30, 50, 100).",
    )


class StatisticalUnitProperties(BaseModel):
    """Statistical unit (census area) feature properties schema.

    Used to allocate residents and compute exposure. Additional properties
    are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")

    statistical_unit_id: int | str = Field(
        description="Unique statistical unit identifier (numerical or string)."
    )
    residents_in_statistical_unit: int = Field(
        ge=0,
        description="Total resident population in this unit.",
    )
    single_parents: float | None = Field(
        default=None,
        ge=0.0,
        description="Total number of single-parent households in this unit.",
    )
    no_highschool_diploma: float = Field(
        ge=0.0,
        description=(
            "Total number of residents without a high-school diploma in the last 3 years "
            "(indicator EDQ for education-based coping capacity)."
        ),
    )
    social_welfare_recipients: float = Field(
        ge=0.0,
        description=(
            "Total number of social welfare recipients in this unit "
            "(indicator WR for welfare-based coping capacity)."
        ),
    )
    elderly_singles_over_65: float = Field(
        ge=0.0,
        description=(
            "Total number of elderly singles over 65 years old in this unit "
            "(indicator ES for elderly singles sensitivity)."
        ),
    )
    children_under_10: float = Field(
        ge=0.0,
        description=(
            "Total number of children under 10 years old in this unit "
            "(indicator C for children sensitivity)."
        ),
    )


# ---------------------------------------------------------------------------
# Typed GeoJSON FeatureCollection aliases (used in late_validate)
# ---------------------------------------------------------------------------

BuildingsFeatureCollection = FeatureCollection[
    Feature[Polygon | MultiPolygon, BuildingProperties]
]
StreetsFeatureCollection = FeatureCollection[
    Feature[Polygon | MultiPolygon, StreetProperties]
]
FloodLayersFeatureCollection = FeatureCollection[
    Feature[Polygon | MultiPolygon, FloodLayerProperties]
]
StatisticalUnitsFeatureCollection = FeatureCollection[
    Feature[Polygon | MultiPolygon, StatisticalUnitProperties]
]


# ---------------------------------------------------------------------------
# Global statistics (for partitionable computation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlobalStats:
    """Computed global statistics for partitionable pipeline steps.

    These values are computed once on the full building dataset by the coordinator
    (split_inputs in BaseParallelProcess) and injected into each worker task. This
    ensures that vulnerability and classification steps use consistent global
    reference points regardless of how data is partitioned.

    Attributes
    ----------
    l2_norms : np.ndarray
        L2 norm for each TOPSIS criterion column.
        Computed as sqrt(sum(M[:, j]**2)) where M is the decision matrix.
        Used to normalize columns in TOPSIS (Step 1: Sensitivity, Coping Capacity, SVI).
    ideal_max : np.ndarray
        Maximum value per TOPSIS criterion column (benefit criteria).
        Computed as M[:, j].max() over all buildings.
        Used to calculate distance to ideal solution in TOPSIS.
    ideal_min : np.ndarray
        Minimum value per TOPSIS criterion column (cost criteria).
        Computed as M[:, j].min() over all buildings.
        Used to calculate distance to worst solution in TOPSIS.
    svi_mean : float
        Mean SVI across all buildings (after Step 1.3 TOPSIS).
        Used as a threshold for the flood sensitivity transform (Step 1.4, Eq. 7):
        SV_PF = (SV + threshold_fraction * svi_mean) ** exponent.

    Notes
    -----
    - TOPSIS criteria are benefit (higher = better) by convention in the paper.
    - If criteria were cost (lower = better), ideal_max and ideal_min would swap roles.
    - Global stats make partitions reproducible and equivalent to city-wide computation.
    - All numeric fields are float64 numpy arrays or Python floats for JSON serialization.
    """
    l2_norms: np.ndarray  # shape: (n_criteria,)
    ideal_max: np.ndarray  # shape: (n_criteria,)
    ideal_min: np.ndarray  # shape: (n_criteria,)
    svi_mean: float


# ---------------------------------------------------------------------------
# Process-level input wrapper
# ---------------------------------------------------------------------------


class ProcessInputs(PipelineParams):
    """Typed wrapper for the OGC exec_body inputs dict.

    Extends ``PipelineParams`` with four GeoJSON FeatureCollection inputs and an
    optional calculation boundary. All pipeline parameters and spatial layers live
    at the same level. FeatureCollections are kept as raw dicts here; structural
    validation via the typed aliases (BuildingsFeatureCollection, etc.) is deferred
    to the ``late_validate`` hook on the process class.

    Validation Pipeline (fastprocesses 3-hook design)
    ================================================

    ProcessInputs participates in three sequential validation phases:

    1. **validate_inputs()** (wire-format; before ProcessInputs construction)
       - Called on raw exec_body['inputs'] before Pydantic parsing
       - For PluvialFloodRiskProcess: checks reference objects ({href, type})
         are well-formed; verifies 'href' is a valid URI; 'type' is in enum
       - Runs SSRF check when allowed_origins is configured
       - Fails fast if wire format is invalid (user-submitted URLs rejected here)
       - Does NOT download or validate data
       - Subclass: override BaseScatterProcess.validate_inputs()

    2. **resolve_remote_inputs()** (downloads and replaces URLs with data)
       - Called by fastprocesses after validate_inputs() succeeds
       - For PluvialFloodRiskProcess: downloads GeoJSON/FlatGeobuf from
         user-supplied {href, type} reference and decodes to GeoJSON dict
         (via fetch_ogc_features + decode_flatgeobuf_bytes)
       - For PluvialFloodRiskHamburg: fetches from server-side URLs when
         user-provided inputs are absent
       - Returns new exec_body['inputs'] with URLs replaced by decoded data
       - Runs SSRF check again on every hop (defense in depth)
       - Subclass: override resolve_remote_inputs()

    3. **late_validate(inputs)** (structure validation after resolution)
       - Called by fastprocesses on the *process* class, not ProcessInputs
       - Validates resolved FeatureCollections via typed aliases (geojson-pydantic)
       - Override late_validate() on PluvialFloodRiskProcess / PluvialFloodRiskHamburg

    See fastprocesses documentation (BaseScatterProcess / BaseParallelProcess)
    for full integration details.
    """

    model_config = {"arbitrary_types_allowed": True}

    buildings: dict = Field(
        description="GeoJSON FeatureCollection of building footprints."
    )
    streets: dict = Field(
        description="GeoJSON FeatureCollection of street/road segments."
    )
    flood_layers: dict = Field(
        description="GeoJSON FeatureCollection of flood depth polygons/grids."
    )
    statistical_units: dict | None = Field(
        default=None,
        description="Optional GeoJSON FeatureCollection of statistical units (census areas).",
    )
    calculation_boundary: dict | None = Field(
        default=None,
        description=(
            "Optional GeoJSON Polygon or MultiPolygon to restrict the geographic "
            "region for scoring and output. Global steps (TOPSIS vulnerability, "
            "classification) still run on the full building dataset; only the "
            "final output features are filtered to this boundary. If null, the "
            "entire study area is processed and returned."
        ),
    )

    @classmethod
    def from_exec_body(cls, exec_body: dict) -> "ProcessInputs":
        """Parse exec_body['inputs'] into a typed ProcessInputs instance.

        All pipeline parameter keys (``vulnerability``, ``hazard``,
        ``risk_weights``, ``output``, ``n_classification_iterations``,
        ``target_crs``) are top-level inputs alongside the spatial layers.
        Any key absent from the user payload falls back to the field default.
        The calculation_boundary field is optional and may be None.

        """
        raw: dict = exec_body["inputs"]
        return cls.model_validate(raw)
