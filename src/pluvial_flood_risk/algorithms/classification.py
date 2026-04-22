"""Module 6 — Risk classification (Step 6, Head/Tail Breaks).

Implements iterative mean classification (Head/Tail Breaks) from
Vogelbacher et al. (2025).  The algorithm repeatedly computes the mean of the
current value set and retains only the values that exceed it:

    iteration 1: mean(all)         → break₀ ; keep values > break₀
    iteration 2: mean(tail₁)       → break₁ ; keep values > break₁
    ...
    iteration n: mean(tailₙ₋₁)    → breakₙ₋₁ (or stop early if tail is empty)

The n break thresholds define n+1 ordinal risk classes:

    class 0: value ≤ break₀            (no / very low risk)
    class k: break_{k-1} < value ≤ break_k   (for k = 1 … n-1)
    class n: value > break_{n-1}        (very high risk)

Two indices are classified independently: PFR_WB and PFR_MA.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from ..models.field_names import (
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    RISK_CLASS_MOBILITY_ACCESSIBILITY,
    RISK_CLASS_WELL_BEING,
)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClassificationResult:
    """Output of :func:`run_classification_pipeline`.

    Attributes
    ----------
    data:
        Input DataFrame extended with two new columns:
        ``risk_class_well_being`` and ``risk_class_mobility_accessibility``.
    breaks_well_being:
        Head/Tail Break thresholds for PFR_WB (up to *n_iterations* values).
    breaks_mobility_accessibility:
        Head/Tail Break thresholds for PFR_MA (up to *n_iterations* values).
    """

    data: pd.DataFrame
    breaks_well_being: list[float]
    breaks_mobility_accessibility: list[float]


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def head_tail_breaks(values: pd.Series, n_iterations: int) -> list[float]:
    """Compute Head/Tail Break thresholds via iterative mean.

    NaN values are ignored.  Iteration stops early if no values remain above
    the current mean (degenerate / constant distribution).

    Parameters
    ----------
    values:
        Risk values per building.  NaN entries are excluded from the
        computation but do not raise an error.
    n_iterations:
        Maximum number of break thresholds to produce.  The returned list
        may be shorter if the data collapses before *n_iterations* is
        reached.

    Returns
    -------
    list[float]
        Sorted break thresholds (means), length ≤ *n_iterations*.
    """
    arr = values.dropna().to_numpy(dtype=float)
    breaks: list[float] = []
    for _ in range(n_iterations):
        if arr.size == 0:
            break
        mean_val = float(np.mean(arr))
        breaks.append(mean_val)
        arr = arr[arr > mean_val]  # head/tail: keep only the "tail" (above mean)
    return breaks


def classify_risk(values: pd.Series, breaks: list[float]) -> pd.Series:
    """Assign an ordinal risk class label to each building value.

    The class boundaries are defined by *breaks*:

    * class 0  — value ≤ breaks[0]
    * class k  — breaks[k-1] < value ≤ breaks[k]  (k = 1 … len(breaks)-1)
    * class n  — value > breaks[-1]               (n = len(breaks))

    Parameters
    ----------
    values:
        Risk values per building.  NaN entries yield NaN class labels.
    breaks:
        Break thresholds as returned by :func:`head_tail_breaks`.  May be
        empty, in which case every non-NaN value is assigned to class 0.

    Returns
    -------
    pd.Series
        Nullable integer class labels (``pd.Int64Dtype``), same index as
        *values*.
    """
    # np.digitize with right=True gives:
    #   0          for x ≤ bins[0]
    #   k          for bins[k-1] < x ≤ bins[k]
    #   len(bins)  for x > bins[-1]
    # Works correctly with an empty bins array (all values → class 0).
    valid = values.dropna()
    result = pd.Series(pd.NA, index=values.index, dtype="Int64")
    classes = np.digitize(
        valid.to_numpy(dtype=float), np.array(breaks, dtype=float), right=True
    )
    result.loc[valid.index] = classes
    return result


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def run_classification_pipeline(
    df: pd.DataFrame,
    n_iterations: int,
) -> ClassificationResult:
    """Apply Head/Tail Breaks classification to both PFR_WB and PFR_MA.

    Expects *df* to contain the two risk columns produced by
    :func:`~pluvial_flood_risk.algorithms.risk.run_risk_pipeline`:

    - ``pluvial_flood_risk_well_being``
    - ``pluvial_flood_risk_mobility_accessibility``

    Parameters
    ----------
    df:
        DataFrame with one row per building.
    n_iterations:
        Number of Head/Tail Breaks iterations (= number of break thresholds).
        Passed through to :func:`head_tail_breaks` for each index.

    Returns
    -------
    ClassificationResult
        Contains the extended DataFrame and the two sets of break thresholds.
    """
    breaks_wb = head_tail_breaks(df[PLUVIAL_FLOOD_RISK_WELL_BEING], n_iterations)
    breaks_ma = head_tail_breaks(
        df[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY], n_iterations
    )

    result = df.copy()
    result[RISK_CLASS_WELL_BEING] = classify_risk(
        df[PLUVIAL_FLOOD_RISK_WELL_BEING], breaks_wb
    )
    result[RISK_CLASS_MOBILITY_ACCESSIBILITY] = classify_risk(
        df[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY], breaks_ma
    )

    return ClassificationResult(
        data=result,
        breaks_well_being=breaks_wb,
        breaks_mobility_accessibility=breaks_ma,
    )
