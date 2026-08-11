"""Bootstrap CIs, Holm-Bonferroni FWER control, non-inferiority test.

Matches the dissertation Ch 6 §Statistical analysis plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable


def bootstrap_ci(
    values: list[float],
    statistic: Callable[[np.ndarray], float],
    n_resamples: int = 10000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Return (point, lo, hi) at 1-alpha confidence."""
    if not values:
        return 0.0, 0.0, 0.0
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    boots = np.empty(n_resamples)
    n = len(arr)
    for i in range(n_resamples):
        sample = rng.choice(arr, size=n, replace=True)
        boots[i] = statistic(sample)
    point = float(statistic(arr))
    lo = float(np.quantile(boots, alpha / 2))
    hi = float(np.quantile(boots, 1 - alpha / 2))
    return point, lo, hi


def holm_bonferroni(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Return per-test reject/accept decisions under Holm-Bonferroni FWER."""
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    n = len(p_values)
    decisions = [False] * n
    rejecting = True
    for rank, (original_index, p) in enumerate(indexed):
        threshold = alpha / (n - rank)
        if rejecting and p <= threshold:
            decisions[original_index] = True
        else:
            rejecting = False
    return decisions


@dataclass(frozen=True, slots=True)
class NonInferiorityResult:
    """Outcome of one non-inferiority test against a pre-committed margin."""

    dimension: str
    difference: float
    upper_bound: float
    margin: float
    p_value: float | None
    passed: bool


def non_inferiority_test(
    monolithic_f1: list[float],
    least_privilege_f1: list[float],
    margin: float = 0.03,
    n_resamples: int = 10000,
    seed: int = 0,
    dimension: str = "macro_f1",
) -> NonInferiorityResult:
    """One-sided paired bootstrap NI test: monolithic - least_privilege <= margin."""
    arr_mono = np.asarray(monolithic_f1, dtype=float)
    arr_lp = np.asarray(least_privilege_f1, dtype=float)
    if len(arr_mono) != len(arr_lp) or len(arr_mono) == 0:
        return NonInferiorityResult(dimension, 0.0, 0.0, margin, None, False)
    diffs = arr_mono - arr_lp
    point_diff = float(np.mean(diffs))

    rng = np.random.default_rng(seed)
    boots = np.empty(n_resamples)
    n = len(diffs)
    for i in range(n_resamples):
        sample = rng.choice(diffs, size=n, replace=True)
        boots[i] = float(np.mean(sample))
    upper_bound = float(np.quantile(boots, 0.975))
    passed = upper_bound < margin
    # Shift the null TO the margin, not BY it. Subtracting the margin alone
    # centres the null on the very statistic it is compared against, which
    # pins the p-value at about 0.5 for every input.
    centered = diffs - point_diff + margin
    rng2 = np.random.default_rng(seed + 1)
    null = np.empty(n_resamples)
    for i in range(n_resamples):
        sample = rng2.choice(centered, size=n, replace=True)
        null[i] = float(np.mean(sample))
    p_value = float(np.mean(null <= point_diff))

    return NonInferiorityResult(
        dimension=dimension,
        difference=point_diff,
        upper_bound=upper_bound,
        margin=margin,
        p_value=p_value,
        passed=passed,
    )
