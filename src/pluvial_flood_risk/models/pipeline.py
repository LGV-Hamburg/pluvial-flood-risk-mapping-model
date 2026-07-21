"""Internal pipeline models — not part of the user-facing process input schema.

These dataclasses carry intermediate state that flows between pipeline stages
(coordinator → workers) and are never exposed in process descriptions or
validated as user inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Global statistics (for partitionable computation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopsisStats:
    """Global statistics needed to replay one TOPSIS step on a data partition.

    Computed from the full (city-wide) decision matrix for a specific TOPSIS
    invocation and injected into workers so that normalisation and ideal
    distances are consistent regardless of partition boundaries.

    All arrays have length ``n_criteria`` for the specific TOPSIS call —
    steps 1.1, 1.2, and 1.3 each produce their own ``TopsisStats`` because
    they operate on different column sets (and thus different matrix shapes).

    Attributes
    ----------
    l2_norms : np.ndarray
        Column-wise L2 norm: ``sqrt(sum(M[:, j]**2))``, shape ``(n_criteria,)``.
        Used in TOPSIS Step 1 to normalise each column of the partition matrix
        the same way as the full dataset.
    ideal_max : np.ndarray
        Column-wise maximum of the normalised + weighted full-dataset matrix,
        shape ``(n_criteria,)``.  Used in TOPSIS Step 2 to compute distance
        from the positive ideal.
    ideal_min : np.ndarray
        Column-wise minimum of the normalised + weighted full-dataset matrix,
        shape ``(n_criteria,)``.  Used in TOPSIS Step 2 to compute distance
        from the negative ideal.

    Notes
    -----
    - All arrays are computed **after** L2 normalisation and weight application
      (i.e. on the weighted-normalised matrix ``M``, not the raw inputs).
    - Serialise to Celery via ``arr.tolist()``; reconstruct with ``np.array()``.
    """

    l2_norms: np.ndarray   # shape: (n_criteria,)
    ideal_max: np.ndarray  # shape: (n_criteria,) — post-normalisation+weighting
    ideal_min: np.ndarray  # shape: (n_criteria,) — post-normalisation+weighting


@dataclass(frozen=True)
class GlobalStats:
    """All city-wide statistics required to run the vulnerability pipeline
    on a data partition without introducing partition-boundary bias.

    Computed once by the coordinator (``split_inputs``) on the full building
    dataset and injected verbatim into each worker (``execute_single``) task.

    Each TOPSIS step has its own ``TopsisStats`` because steps 1.1, 1.2,
    and 1.3 operate on matrices with different column sets and therefore
    different array lengths.

    Attributes
    ----------
    sensitivity : TopsisStats
        Stats for Step 1.1 (Sensitivity TOPSIS).
        n_criteria = 2 (or 3 when ``single_parents`` weight > 0).
    coping_capacity : TopsisStats
        Stats for Step 1.2 (Coping Capacity TOPSIS).
        n_criteria = 2.
    svi : TopsisStats
        Stats for Step 1.3 (Social Vulnerability Index TOPSIS).
        n_criteria = 2.
    svi_mean : float
        City-wide mean of ``social_vulnerability_index`` (after Step 1.3).
        Used as the threshold floor in the Step 1.4 flood susceptibility
        transform: ``SV_PF = (SV + threshold_fraction * svi_mean) ** exponent``.
    """

    sensitivity: TopsisStats
    coping_capacity: TopsisStats
    svi: TopsisStats
    svi_mean: float
