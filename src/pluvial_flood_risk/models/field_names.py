"""Central field-name constants for the pluvial flood risk pipeline.

Mapping from the original arcpy/ArcGIS field names (Vogelbacher et al., 2025)
to explicit, self-documenting names used throughout this package.

Original → New
--------------
C          → CHILDREN_UNDER_10
ES         → ELDERLY_SINGLES_OVER_65
WR         → SOCIAL_WELFARE_RECIPIENTS
EDQ        → NO_HIGHSCHOOL_DIPLOMA
Sen        → SENSITIVITY_INDEX
CCap       → COPING_CAPACITY_INDEX
SV         → SOCIAL_VULNERABILITY_INDEX
SV_PF      → SOCIAL_VULNERABILITY_FLOOD_POTENTIAL
Floors     → FLOORS_ABOVE_GROUND
Building_type → BUILDING_TYPE
StatisticalUnit → STATISTICAL_UNIT_ID
Residents  → RESIDENTS_IN_STATISTICAL_UNIT
LivingArea → LIVING_AREA_M2_STATISTICAL_UNIT
Area_house → BUILDING_LIVING_AREA_M2
R          → RESIDENTS_PER_BUILDING
R_G / EWB  → RESIDENTS_GROUND_FLOOR
EMA        → EXPOSURE_MOBILITY_ACCESSIBILITY
HMA        → HAZARD_MOBILITY_ACCESSIBILITY
HWB        → HAZARD_WELL_BEING
PFR_WB     → PLUVIAL_FLOOD_RISK_WELL_BEING
PFR_MA     → PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Vulnerability — raw socio-economic indicators
# ---------------------------------------------------------------------------

CHILDREN_UNDER_10: str = "children_under_10"
"""Number of children under 10 years (sensitivity indicator C)."""

ELDERLY_SINGLES_OVER_65: str = "elderly_singles_over_65"
"""Number of elderly singles over 65 years (sensitivity indicator ES)."""

SINGLE_PARENTS: str = "single_parents"
"""Number of single-parent households (optional sensitivity indicator SP)."""

SOCIAL_WELFARE_RECIPIENTS: str = "social_welfare_recipients"
"""Number of social welfare recipients (coping capacity indicator WR)."""

NO_HIGHSCHOOL_DIPLOMA: str = "no_highschool_diploma"
"""Number of residents without high-school diploma in last 3 years (coping capacity indicator EDQ)."""

# ---------------------------------------------------------------------------
# Vulnerability — computed indices (Steps 1.1-1.4)
# ---------------------------------------------------------------------------

SENSITIVITY_INDEX: str = "sensitivity_index"
"""TOPSIS closeness coefficient for sensitivity (Step 1.1). Range: [0, 2] with entropy."""

COPING_CAPACITY_INDEX: str = "coping_capacity_index"
"""TOPSIS closeness coefficient for coping capacity (Step 1.2). Range: [0, 2] with entropy."""

SOCIAL_VULNERABILITY_INDEX: str = "social_vulnerability_index"
"""Aggregated social vulnerability index SV (Step 1.3)."""

SOCIAL_VULNERABILITY_FLOOD_POTENTIAL: str = "social_vulnerability_flood_potential"
"""Flood-sensitivity-transformed SVI SV_PF (Step 1.4, Eq. 7)."""

# ---------------------------------------------------------------------------
# Exposure — building attributes (Step 2)
# ---------------------------------------------------------------------------

BUILDING_FOOTPRINT_AREA_M2: str = "building_footprint_area_m2"
"""Footprint area of the building polygon [m²] (Shape_Area). Derived from geometry."""

FLOORS_ABOVE_GROUND: str = "floors_above_ground"
"""Number of floors above ground."""

BUILDING_TYPE: str = "building_type"
"""Building type code (1 = residential ground-floor only, 2 = mixed, …)."""

STATISTICAL_UNIT_ID: str = "statistical_unit_id"
"""Reference key linking building to its statistical unit."""

RESIDENTS_IN_STATISTICAL_UNIT: str = "residents_in_statistical_unit"
"""Total residents registered in the statistical unit."""

LIVING_AREA_M2_STATISTICAL_UNIT: str = "living_area_m2_statistical_unit"
"""Total residential floor area [m²] of the statistical unit."""

BUILDING_LIVING_AREA_M2: str = "building_living_area_m2"
"""Net usable floor area of the building (Netto-Etagen x footprint area) [m²]."""

RESIDENTS_PER_BUILDING: str = "residents_per_building"
"""Area-weighted resident count for the building R (Step 2, Eq. 8)."""

RESIDENTS_GROUND_FLOOR: str = "residents_ground_floor"
"""Ground-floor resident count R_G / exposure index EWB (Step 2, Eq. 9)."""

EXPOSURE_MOBILITY_ACCESSIBILITY: str = "exposure_mobility_accessibility"
"""Mobility & accessibility exposure index EMA (Step 2, Eq. 10)."""

# ---------------------------------------------------------------------------
# Hazard — intermediate spatial outputs (Steps 3.1 / 4.1)
# ---------------------------------------------------------------------------

FLOODED_AREA_M2_5M: str = "flooded_area_m2_5m"
"""Flooded area [m²] within the 5 m building buffer (F30A5m)."""

FLOODED_FRACTION_5M: str = "flooded_fraction_5m"
"""Flooded fraction [0-1] within the 5 m building buffer (F30P5m)."""

FLOODED_AREA_M2_15M: str = "flooded_area_m2_15m"
"""Flooded area [m²] within the 15 m street buffer zone (F30A15m)."""

FLOODED_FRACTION_15M: str = "flooded_fraction_15m"
"""Flooded fraction [0-1] within the 15 m street buffer zone (F30P15m)."""

FLOODED_AREA_M2_30M: str = "flooded_area_m2_30m"
"""Flooded area [m²] within the 15-30 m street buffer zone (F30A30m)."""

FLOODED_FRACTION_30M: str = "flooded_fraction_30m"
"""Flooded fraction [0-1] within the 15-30 m street buffer zone (F30P30m)."""

# ---------------------------------------------------------------------------
# Hazard — computed indices (Steps 3.2 / 4.2)
# ---------------------------------------------------------------------------

HAZARD_WELL_BEING: str = "hazard_well_being"
"""Hazard index for well-being / health HWB (Step 4.2, Eq. 12)."""

HAZARD_MOBILITY_ACCESSIBILITY: str = "hazard_mobility_accessibility"
"""Hazard index for mobility & accessibility HMA (Step 3.2, Eq. 13)."""

# ---------------------------------------------------------------------------
# Risk output (Step 5)
# ---------------------------------------------------------------------------

PLUVIAL_FLOOD_RISK_WELL_BEING: str = "pluvial_flood_risk_well_being"
"""Pluvial flood risk index for well-being PFR_WB (Step 5, Eq. 14)."""

PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY: str = (
    "pluvial_flood_risk_mobility_accessibility"
)
"""Pluvial flood risk index for mobility & accessibility PFR_MA (Step 5, Eq. 15)."""

# ---------------------------------------------------------------------------
# Classification output (Step 6)
# ---------------------------------------------------------------------------

RISK_CLASS_WELL_BEING: str = "risk_class_well_being"
"""Ordinal risk class for well-being (0 = no risk … n = very high risk) from Head/Tail Breaks (Step 6)."""

RISK_CLASS_MOBILITY_ACCESSIBILITY: str = "risk_class_mobility_accessibility"
"""Ordinal risk class for mobility & accessibility (Step 6)."""
