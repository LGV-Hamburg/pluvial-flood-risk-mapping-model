"""Pydantic parameter models for the pluvial flood risk pipeline.

All classes carry paper-derived defaults from Vogelbacher et al. (2025).
Fields are fully optional at construction — override only what differs from
the paper.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Vulnerability (Steps 1.1-1.4)
# ---------------------------------------------------------------------------


class SensitivityWeights(BaseModel):
    """Attribute weights for the sensitivity TOPSIS step (Step 1.1).

    Defaults from Vogelbacher et al. (2025), Table / line 155:
        ES (elderly singles) = 0.7, C (children) = 0.3.
    """

    children_under_10: float = Field(default=0.3, ge=0.0, le=1.0)
    elderly_singles_over_65: float = Field(default=0.7, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> SensitivityWeights:
        total = self.children_under_10 + self.elderly_singles_over_65
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"SensitivityWeights must sum to 1.0, got {total:.6f}"
            )
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
            raise ValueError(
                f"CopingCapacityWeights must sum to 1.0, got {total:.6f}"
            )
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
            raise ValueError(
                f"SVIWeights must sum to 1.0, got {total:.6f}"
            )
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
        description="Buffer radius [m] around building footprint for the well-being hazard (Step 4.1).",
    )
    buffer_zone1_m: float = Field(
        default=5.0,
        gt=0.0,
        description="Inner buffer radius [m] around building for mobility hazard (Step 3.1).",
    )
    buffer_zone2_m: float = Field(
        default=15.0,
        gt=0.0,
        description="Street buffer radius [m] for the near-access zone (Step 3.1).",
    )
    buffer_zone3_m: float = Field(
        default=30.0,
        gt=0.0,
        description="Street buffer outer radius [m] for the extended-access zone (Step 3.1).",
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


# ---------------------------------------------------------------------------
# Risk weights (Step 5)
# ---------------------------------------------------------------------------


class RiskWeights(BaseModel):
    """Exponent weights for the IPCC risk product formula (Step 5, Eq. 14/15).

    PFR = SV_PF^a * E^b * H^c

    All default to 1.0 (unweighted product) as in the paper.
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

    vulnerability: VulnerabilityParams = Field(default_factory=VulnerabilityParams)
    hazard: HazardParams = Field(default_factory=HazardParams)
    risk_weights: RiskWeights = Field(default_factory=RiskWeights)
    n_classification_iterations: int = Field(
        default=4,
        ge=1,
        description=(
            "Number of iterations for the head/tail-breaks classification (Step 6). "
            "Yields n+1 risk classes (no risk … very high risk)."
        ),
    )
