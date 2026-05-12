"""Algorithm modules for the pluvial flood risk pipeline."""

from .classification import (
    ClassificationResult,
    classify_risk,
    head_tail_breaks,
    run_classification_pipeline,
)
from .exposure import (
    compute_building_living_area,
    compute_exposure_mobility_accessibility,
    compute_residents_ground_floor,
    compute_residents_per_building,
    run_exposure_pipeline,
)
from .hazard_ma import (
    compute_hazard_ma,
    compute_mobility_flood_fractions,
    run_hazard_ma_pipeline,
)
from .hazard_wb import (
    compute_hazard_wb,
    compute_wb_flood_fractions,
    flooded_area_col,
    flooded_fraction_col,
    run_hazard_wb_pipeline,
)
from .risk import compute_risk, run_risk_pipeline
from .topsis import topsis
from .vulnerability import (
    compute_coping_capacity,
    compute_sensitivity,
    compute_svi,
    flood_susceptibility_transform,
    run_vulnerability_pipeline,
)

__all__ = [
    "ClassificationResult",
    "head_tail_breaks",
    "classify_risk",
    "run_classification_pipeline",
    "topsis",
    "compute_sensitivity",
    "compute_coping_capacity",
    "compute_svi",
    "flood_susceptibility_transform",
    "run_vulnerability_pipeline",
    "compute_building_living_area",
    "compute_residents_per_building",
    "compute_residents_ground_floor",
    "compute_exposure_mobility_accessibility",
    "run_exposure_pipeline",
    "compute_mobility_flood_fractions",
    "compute_hazard_ma",
    "run_hazard_ma_pipeline",
    "flooded_fraction_col",
    "flooded_area_col",
    "compute_wb_flood_fractions",
    "compute_hazard_wb",
    "run_hazard_wb_pipeline",
    "compute_risk",
    "run_risk_pipeline",
]
