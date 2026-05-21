"""Module 5 — Risk calculation (Step 5, Eq. 14/15).

Implements the IPCC risk product formula from Vogelbacher et al. (2025):

    PFR = SV_PF^a  ×  E^b  ×  H^c

Two risk indices are computed per building:

    PFR_WB  (well-being)
        SV_PF = social_vulnerability_flood_potential
        E     = residents_ground_floor            (EWB)
        H     = hazard_well_being                 (HWB)

    PFR_MA  (mobility & accessibility)
        SV_PF = social_vulnerability_flood_potential  (same)
        E     = exposure_mobility_accessibility   (EMA)
        H     = hazard_mobility_accessibility     (HMA)

Exponents a, b, c default to 1.0 (unweighted product as in the paper).
Non-default exponents can be supplied via ``RiskWeights``.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from ..models.field_names import (
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    RESIDENTS_GROUND_FLOOR,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
)
from ..models.inputs import RiskWeights

logger = logging.getLogger(__name__)


def compute_risk(
    sv_pf: pd.Series,
    exposure: pd.Series,
    hazard: pd.Series,
    params: RiskWeights,
) -> pd.Series:
    """Compute a single pluvial flood risk index (Eq. 14/15).

    PFR = SV_PF^a × E^b × H^c

    All three input series must share the same index.  Values are clipped
    to [0, ∞) before the power is applied so that minor floating-point
    negatives (e.g. −1e-16 from spatial overlays) do not propagate.

    Parameters
    ----------
    sv_pf:
        Social vulnerability flood potential per building.
    exposure:
        Exposure index per building (EWB or EMA).
    hazard:
        Hazard index per building (HWB or HMA).
    params:
        Exponent weights (a, b, c).  Defaults all 1.0.

    Returns
    -------
    pd.Series
        Risk index values, same index as inputs.
    """
    v = sv_pf.clip(lower=0.0) ** params.vulnerability_exponent
    e = exposure.clip(lower=0.0) ** params.exposure_exponent
    h = hazard.clip(lower=0.0) ** params.hazard_exponent
    return (v * e * h).rename(None)


def run_risk_pipeline(
    df: pd.DataFrame,
    params: RiskWeights,
) -> pd.DataFrame:
    """Compute PFR_WB and PFR_MA for all buildings (Step 5).

    Expects *df* to already contain the four component columns produced by
    the vulnerability, exposure, and hazard pipelines:

    - ``social_vulnerability_flood_potential``
    - ``residents_ground_floor``
    - ``exposure_mobility_accessibility``
    - ``hazard_well_being``
    - ``hazard_mobility_accessibility``

    Parameters
    ----------
    df:
        DataFrame with one row per building and at least the five columns
        listed above.
    params:
        Exponent weights for the risk formula.

    Returns
    -------
    pd.DataFrame
        *df* with two additional columns:
        ``pluvial_flood_risk_well_being`` and
        ``pluvial_flood_risk_mobility_accessibility``.
    """
    logger.info("Risk pipeline start: %d buildings", len(df))
    _t0 = time.perf_counter()
    result = df.copy()
    result[PLUVIAL_FLOOD_RISK_WELL_BEING] = compute_risk(
        df[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL],
        df[RESIDENTS_GROUND_FLOOR],
        df[HAZARD_WELL_BEING],
        params,
    )
    result[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY] = compute_risk(
        df[SOCIAL_VULNERABILITY_FLOOD_POTENTIAL],
        df[EXPOSURE_MOBILITY_ACCESSIBILITY],
        df[HAZARD_MOBILITY_ACCESSIBILITY],
        params,
    )
    logger.info("Risk pipeline complete: %.2fs", time.perf_counter() - _t0)
    return result
