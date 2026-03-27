"""TOPSIS - Technique for Order of Preference by Similarity to Ideal Solution.

Pure NumPy implementation used by the vulnerability pipeline
(Vogelbacher et al., 2025, Eq. 4-6).

Convention used throughout
--------------------------
For vulnerability indicators, *lower* is better (less vulnerable).
The positive ideal solution A⁺ is therefore the **minimum** of each column
and the negative ideal A⁻ is the **maximum**.

The closeness coefficient is defined as:

    C_i = D_from_min / (D_from_max + D_from_min)

where D_from_max and D_from_min are the Euclidean distances of alternative i
to the column-wise maximum and minimum respectively.  This gives C=0 for the
least-vulnerable alternative and C=1 for the most-vulnerable one — consistent
with the original ArcPy scratch scripts.

When *apply_entropy* is True, each C_i is multiplied by the Shannon entropy
index U_i ∈ [1, 2] (Eq. 6):

    U_i = 2 + (1 / ln(n)) * Σ_j (p_ij * ln(p_ij))

where p_ij = z_ij / Σ_j z_ij and z_ij are the weighted-normalised values.
U_i = 2 (maximum) when any indicator is zero (most heterogeneous assumption).
U_i = 1 (minimum) when all indicators are equal (uniform distribution).

The output range is [0, 1] without entropy and [0, 2] with entropy.
"""

from __future__ import annotations

import math

import numpy as np


def topsis(
    matrix: np.ndarray,
    weights: np.ndarray,
    *,
    apply_entropy: bool = True,
) -> np.ndarray:
    """Compute TOPSIS closeness coefficients for a decision matrix.

    Parameters
    ----------
    matrix:
        Array of shape ``(m, n)`` — *m* alternatives (buildings) × *n* criteria
        (socio-economic indicators).  Values must be non-negative finite floats;
        pass zeros for any original NULL/missing values before calling this
        function.  Rows that contained NULL inputs should be masked by the
        caller and assigned ``np.nan`` in the output.
    weights:
        1-D array of length *n*.  Must be non-negative and sum to 1.0.
    apply_entropy:
        If ``True`` (default), multiply each closeness coefficient by the
        Shannon entropy index U_i (Eq. 6).

    Returns
    -------
    np.ndarray
        Shape ``(m,)``.  Values in ``[0, 1]`` without entropy,
        or ``[0, 2]`` with entropy.
    """
    if matrix.ndim != 2:
        raise ValueError(f"matrix must be 2-D, got shape {matrix.shape}")
    m, n = matrix.shape
    if weights.shape != (n,):
        raise ValueError(
            f"weights shape {weights.shape} does not match matrix columns {n}"
        )

    # Work on a copy so the caller's array is never mutated.
    M = matrix.astype(float, copy=True)

    # ------------------------------------------------------------------
    # Step 1 — Column-wise L2 normalisation + weighting  (Eq. 4)
    # ------------------------------------------------------------------
    for j in range(n):
        col_sq_sum = float(np.sum(M[:, j] ** 2))
        if col_sq_sum == 0.0:
            M[:, j] = 0.0
        else:
            M[:, j] = M[:, j] / math.sqrt(col_sq_sum) * weights[j]

    # ------------------------------------------------------------------
    # Step 2 — Euclidean distances from column-wise max and min  (Eq. 5)
    # ------------------------------------------------------------------
    ideal_max = M.max(axis=0)
    ideal_min = M.min(axis=0)

    d_from_max = np.sqrt(np.sum((M - ideal_max) ** 2, axis=1))
    d_from_min = np.sqrt(np.sum((M - ideal_min) ** 2, axis=1))

    # ------------------------------------------------------------------
    # Step 3 — Closeness coefficient  C_i = D_from_min / (D_from_max + D_from_min)
    # ------------------------------------------------------------------
    denom = d_from_max + d_from_min
    # np.divide with out+where avoids evaluating the division at zero-denom
    # positions, preventing a RuntimeWarning from np.where's eager evaluation.
    C = np.divide(d_from_min, denom, out=np.zeros_like(d_from_min), where=denom > 0.0)

    if not apply_entropy:
        return C

    # ------------------------------------------------------------------
    # Step 4 — Shannon entropy index  U_i  (Eq. 6)
    # ------------------------------------------------------------------
    # Use the already weighted-normalised M (same convention as the
    # original ArcPy scripts).
    U = np.full(m, 2.0)
    ln_n = math.log(n)

    for i in range(m):
        row = M[i]
        # If any element is zero, entropy defaults to 2 (maximum).
        if np.count_nonzero(row) < n:
            continue
        row_sum = row.sum()
        if row_sum == 0.0:
            continue
        p = row / row_sum
        U[i] = 2.0 + (1.0 / ln_n) * float(np.sum(p * np.log(p)))

    return C * U
