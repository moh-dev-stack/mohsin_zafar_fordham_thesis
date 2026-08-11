"""Tests for dqa.stats.

Rewritten in the third review pass. The previous version asserted five things
that could not fail:

* ``first == second`` on two seeded calls, which tests that seeding works, not
  that the statistic is right. It would have passed against a function that
  returned ``(0.0, 0.0, 0.0)`` for every input.
* ``lo <= observed <= hi`` on a percentile bootstrap of the mean, which holds by
  construction whenever the resampled means straddle the observed mean.
* ``first.difference <= first.upper_bound``, likewise near-automatic.
* ``first.passed is (first.upper_bound < first.margin)``, which is the
  implementation line ``passed = upper_bound < margin`` copied into the test. It
  restates the code rather than checking it.
* A Holm test named ``controls_family_wise_error`` that checked two hard-coded
  vectors and neither the step-down rule nor the ordering the rule depends on.

None of that would have caught the defect this module actually shipped: a
non-inferiority p-value that came back at about 0.50 for every input, including
inputs where the confined system was half a point ahead. That defect was fixed
and then left unpinned, which is the gap this file closes first.

The replacements are behavioural. Where a property is genuinely structural it is
asserted against a case constructed to violate it if the code were wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from dqa.stats import bootstrap_ci, holm_bonferroni, non_inferiority_test

# Fixed paired inputs: monolithic vs least-privilege macro-F1 across 12 folds.
# The monolith leads by about 0.017 on average, comfortably inside a 0.03 margin.
MONOLITHIC = [0.82, 0.79, 0.85, 0.81, 0.88, 0.76, 0.83, 0.80, 0.86, 0.78, 0.84, 0.81]
LEAST_PRIVILEGE = [0.80, 0.78, 0.84, 0.79, 0.85, 0.75, 0.82, 0.79, 0.83, 0.77, 0.82, 0.80]

SEED = 12345
N_RESAMPLES = 2000

MEAN = staticmethod(lambda a: float(np.mean(a)))


def _mean(a) -> float:
    return float(np.mean(a))


def _diffs() -> list[float]:
    return [m - lp for m, lp in zip(MONOLITHIC, LEAST_PRIVILEGE, strict=True)]


# --------------------------------------------------------------- bootstrap_ci


def test_bootstrap_ci_point_estimate_is_the_statistic_on_the_real_sample() -> None:
    """The point is not a resample. It is the statistic on the data itself."""
    point, lo, hi = bootstrap_ci(_diffs(), _mean, n_resamples=N_RESAMPLES, seed=SEED)
    assert point == pytest.approx(_mean(_diffs()), abs=1e-15)
    assert lo < point < hi


def test_bootstrap_ci_covers_a_known_mean_at_about_the_nominal_rate() -> None:
    """Coverage, which is the only property that makes an interval an interval.

    Draws 200 independent samples from a distribution whose mean is known, and
    counts how often the returned interval contains it. A percentile bootstrap at
    n = 40 undercovers slightly, so the band is deliberately wide; the point is to
    fail an interval that covers 60 per cent of the time or 100 per cent of the
    time, not to certify a precise rate.
    """
    rng = np.random.default_rng(20260805)
    true_mean = 0.25
    covered = 0
    trials = 200
    for trial in range(trials):
        sample = rng.normal(loc=true_mean, scale=1.0, size=40).tolist()
        _, lo, hi = bootstrap_ci(sample, _mean, n_resamples=400, seed=trial)
        covered += lo <= true_mean <= hi
    rate = covered / trials
    assert 0.85 <= rate <= 0.99, f"coverage {rate:.3f} is not near the nominal 0.95"


def test_bootstrap_ci_excludes_a_mean_the_data_rules_out() -> None:
    """The interval must be able to say no. A CI that never excludes is useless."""
    sample = [5.0, 5.1, 4.9, 5.05, 4.95, 5.02, 4.98, 5.01]
    _, lo, hi = bootstrap_ci(sample, _mean, n_resamples=2000, seed=1)
    assert lo <= 5.0 <= hi
    assert not (lo <= 0.0 <= hi), "an interval around 5.0 must exclude 0.0"
    assert not (lo <= 10.0 <= hi)


def test_bootstrap_ci_narrows_as_the_sample_grows() -> None:
    """More data, tighter interval. A width that ignores n is not a bootstrap."""
    rng = np.random.default_rng(7)
    small = rng.normal(0.0, 1.0, size=20).tolist()
    large = rng.normal(0.0, 1.0, size=500).tolist()
    _, lo_s, hi_s = bootstrap_ci(small, _mean, n_resamples=1000, seed=3)
    _, lo_l, hi_l = bootstrap_ci(large, _mean, n_resamples=1000, seed=3)
    assert (hi_l - lo_l) < (hi_s - lo_s) / 2


def test_bootstrap_ci_respects_alpha() -> None:
    """A 99 per cent interval must contain the 95 per cent one."""
    sample = _diffs()
    _, lo95, hi95 = bootstrap_ci(sample, _mean, n_resamples=4000, alpha=0.05, seed=11)
    _, lo99, hi99 = bootstrap_ci(sample, _mean, n_resamples=4000, alpha=0.01, seed=11)
    assert lo99 <= lo95 and hi99 >= hi95
    assert (hi99 - lo99) > (hi95 - lo95)


def test_bootstrap_ci_is_seed_determined_but_not_seed_independent() -> None:
    """Seeding fixes the interval; the point estimate does not depend on it.

    Stronger than the old ``first == second``: that would have passed for a
    constant function. This additionally requires the seed to actually drive the
    resampling, and the point estimate to be free of it.
    """
    a = bootstrap_ci(_diffs(), _mean, n_resamples=500, seed=SEED)
    b = bootstrap_ci(_diffs(), _mean, n_resamples=500, seed=SEED)
    c = bootstrap_ci(_diffs(), _mean, n_resamples=500, seed=SEED + 1)
    assert a == b
    assert a[0] == c[0], "the point estimate must not depend on the seed"
    assert (a[1], a[2]) != (c[1], c[2]), "the interval must depend on the seed"


def test_bootstrap_ci_works_for_a_statistic_other_than_the_mean() -> None:
    """``statistic`` is a parameter, so it has to be honoured.

    The same outlier-laden sample through the median and through the mean. If
    ``bootstrap_ci`` ignored its ``statistic`` argument the two intervals would
    coincide; instead the median interval is two orders of magnitude tighter.
    """
    sample = [*range(1, 20), 10000.0]
    med_point, med_lo, med_hi = bootstrap_ci(
        sample, lambda a: float(np.median(a)), n_resamples=2000, seed=5
    )
    mean_point, _, mean_hi = bootstrap_ci(sample, _mean, n_resamples=2000, seed=5)

    assert med_point == pytest.approx(10.5)
    assert med_hi < 20.0, "a median interval must not be dragged out by the outlier"
    assert mean_point > 500.0, "the mean interval must be dragged out by it"
    assert (med_hi - med_lo) < (mean_hi - med_lo) / 100


def test_bootstrap_ci_on_empty_input() -> None:
    assert bootstrap_ci([], _mean, n_resamples=10) == (0.0, 0.0, 0.0)


# --------------------------------------------------------- non_inferiority_test


def test_non_inferiority_passes_when_the_gap_is_well_inside_the_margin() -> None:
    result = non_inferiority_test(
        MONOLITHIC, LEAST_PRIVILEGE, margin=0.03, n_resamples=N_RESAMPLES, seed=SEED
    )
    assert result.difference == pytest.approx(_mean(_diffs()), abs=1e-12)
    assert result.upper_bound < 0.03
    assert result.passed is True


def test_non_inferiority_fails_when_the_gap_exceeds_the_margin() -> None:
    """Constructed to fail. The old suite had no failing case at all."""
    behind = [v - 0.20 for v in MONOLITHIC]
    result = non_inferiority_test(
        MONOLITHIC, behind, margin=0.03, n_resamples=N_RESAMPLES, seed=SEED
    )
    assert result.difference == pytest.approx(0.20, abs=1e-9)
    assert result.upper_bound > 0.03
    assert result.passed is False


def test_non_inferiority_passes_when_the_confined_arm_is_ahead() -> None:
    ahead = [v + 0.05 for v in MONOLITHIC]
    result = non_inferiority_test(
        MONOLITHIC, ahead, margin=0.03, n_resamples=N_RESAMPLES, seed=SEED
    )
    assert result.difference < 0
    assert result.passed is True


def test_the_p_value_is_not_pinned_near_one_half_REGRESSION() -> None:
    """The defect this module shipped, pinned at last.

    The null used to be centred by subtracting the margin from the differences,
    which centres it on the very statistic it is compared against, so
    ``mean(null <= point_diff)`` came back at about 0.50 for every input --
    including one where the confined system led by half a point. The fix shifts
    the null TO the margin instead.

    A p-value that ignores its input is the failure mode, so this asserts the
    p-value MOVES: far inside the margin it must be small, far outside it must be
    large, and the two must be nowhere near each other.
    """
    far_inside = non_inferiority_test(
        MONOLITHIC, [v + 0.50 for v in MONOLITHIC], margin=0.03, n_resamples=4000, seed=SEED
    )
    far_outside = non_inferiority_test(
        MONOLITHIC, [v - 0.50 for v in MONOLITHIC], margin=0.03, n_resamples=4000, seed=SEED
    )
    assert far_inside.p_value is not None and far_outside.p_value is not None
    assert far_inside.p_value < 0.05, (
        f"a system leading by 0.50 gives p={far_inside.p_value:.4f}; the old code "
        f"returned about 0.50 here"
    )
    assert far_outside.p_value > 0.95
    assert abs(far_inside.p_value - far_outside.p_value) > 0.9


def test_the_p_value_is_monotone_in_the_observed_gap() -> None:
    """Worse performance must never make the non-inferiority evidence stronger."""
    p_values = []
    for penalty in (-0.10, -0.02, 0.0, 0.02, 0.10):
        result = non_inferiority_test(
            MONOLITHIC,
            [v - penalty for v in MONOLITHIC],
            margin=0.03,
            n_resamples=4000,
            seed=SEED,
        )
        p_values.append(result.p_value)
    assert p_values == sorted(p_values), f"p-values not monotone in the gap: {p_values}"
    assert p_values[0] < 0.05, f"a 0.10 lead gives p={p_values[0]:.4f}"
    assert p_values[-1] > 0.95, f"a 0.10 deficit gives p={p_values[-1]:.4f}"


def test_non_inferiority_margin_changes_the_decision() -> None:
    """The margin is a parameter, so a decision must depend on it."""
    strict = non_inferiority_test(
        MONOLITHIC, LEAST_PRIVILEGE, margin=0.005, n_resamples=N_RESAMPLES, seed=SEED
    )
    lenient = non_inferiority_test(
        MONOLITHIC, LEAST_PRIVILEGE, margin=0.05, n_resamples=N_RESAMPLES, seed=SEED
    )
    assert strict.passed is False
    assert lenient.passed is True
    assert strict.upper_bound == pytest.approx(lenient.upper_bound, abs=1e-12)


def test_non_inferiority_is_paired_not_two_sample() -> None:
    """Reordering one arm alone must change the result, or the test is unpaired.

    A paired test consumes the per-fold differences, so permuting one arm while
    leaving the other fixed changes every difference and must change the
    interval, even though both marginal distributions are untouched.
    """
    shuffled = list(reversed(LEAST_PRIVILEGE))
    paired = non_inferiority_test(
        MONOLITHIC, LEAST_PRIVILEGE, margin=0.03, n_resamples=N_RESAMPLES, seed=SEED
    )
    repaired = non_inferiority_test(
        MONOLITHIC, shuffled, margin=0.03, n_resamples=N_RESAMPLES, seed=SEED
    )
    assert paired.difference == pytest.approx(repaired.difference, abs=1e-12)
    assert paired.upper_bound != repaired.upper_bound


def test_non_inferiority_test_rejects_mismatched_lengths() -> None:
    result = non_inferiority_test([0.8, 0.7], [0.8], n_resamples=10, seed=SEED)
    assert result.passed is False
    assert result.p_value is None
    assert result.difference == 0.0


def test_non_inferiority_test_rejects_empty_input() -> None:
    result = non_inferiority_test([], [], n_resamples=10, seed=SEED)
    assert result.passed is False
    assert result.p_value is None


def test_non_inferiority_result_carries_its_dimension_and_margin() -> None:
    result = non_inferiority_test(
        MONOLITHIC, LEAST_PRIVILEGE, margin=0.03, n_resamples=100, seed=SEED,
        dimension="f1_plausibility",
    )
    assert result.dimension == "f1_plausibility"
    assert result.margin == 0.03


# ----------------------------------------------------------- holm_bonferroni


def test_holm_is_uniformly_more_powerful_than_bonferroni() -> None:
    """The whole reason to use Holm. The old test never compared the two.

    With three tests at alpha 0.05, plain Bonferroni rejects only p <= 0.01667.
    Holm's second step tests against alpha/2 = 0.025, so p = 0.02 is rejected by
    Holm and not by Bonferroni. A step-down that had been written as a plain
    Bonferroni would pass the old test and fail this one.
    """
    p_values = [0.001, 0.02, 0.30]
    holm = holm_bonferroni(p_values, alpha=0.05)
    bonferroni = [p <= 0.05 / len(p_values) for p in p_values]
    assert holm == [True, True, False]
    assert bonferroni == [True, False, False]
    assert holm[1] and not bonferroni[1]


def test_holm_stops_at_the_first_non_rejection() -> None:
    """Step-down: once a hypothesis survives, everything above it survives too.

    At alpha = 0.3 over three tests: 0.001 clears alpha/3 and is rejected; 0.20
    fails alpha/2 = 0.15 and survives; and the latch then forbids rejecting 0.21
    even though 0.21 clears the rank-3 threshold of alpha/1 = 0.3. A loop that
    tested each rank independently, with no ``rejecting = False`` latch, would
    return ``[True, False, True]`` here and pass the suite this replaced.
    """
    assert holm_bonferroni([0.001, 0.20, 0.21], alpha=0.3) == [True, False, False]

    # And the ordinary consequence of step-down: 0.04 would be significant
    # uncorrected at alpha = 0.05, and is not rejected, because at rank two the
    # threshold is alpha/2 = 0.025.
    assert holm_bonferroni([0.001, 0.20, 0.04], alpha=0.05) == [True, False, False]


def test_holm_decisions_follow_the_inputs_not_their_positions() -> None:
    """Order independence: the decision belongs to the p-value, not to its index."""
    p_values = [0.30, 0.001, 0.02]
    decisions = holm_bonferroni(p_values, alpha=0.05)
    assert decisions == [False, True, True]
    reordered = [0.001, 0.02, 0.30]
    assert holm_bonferroni(reordered, alpha=0.05) == [True, True, False]
    assert sorted(zip(p_values, decisions)) == sorted(
        zip(reordered, holm_bonferroni(reordered, alpha=0.05))
    )


def test_holm_rejects_nothing_when_every_p_is_large() -> None:
    assert holm_bonferroni([0.9, 0.8, 0.7], alpha=0.05) == [False, False, False]


def test_holm_rejects_everything_when_every_p_is_tiny() -> None:
    assert holm_bonferroni([1e-9, 1e-9, 1e-9], alpha=0.05) == [True, True, True]


def test_holm_on_a_single_test_is_the_uncorrected_test() -> None:
    assert holm_bonferroni([0.04], alpha=0.05) == [True]
    assert holm_bonferroni([0.06], alpha=0.05) == [False]


def test_holm_gives_tied_p_values_the_same_decision() -> None:
    """Identical p-values must not be split by their arbitrary sort position.

    Three tied values are decided by the strictest threshold they face, so they
    are rejected together or not at all. 0.01 clears alpha/3; 0.02 does not, and
    the latch then carries that survival to the other two. A step-down that
    forgot the latch would reject two of the three tied 0.02 values, which is
    indefensible for values that are equal.
    """
    assert holm_bonferroni([0.01, 0.01, 0.01], alpha=0.05) == [True, True, True]
    assert holm_bonferroni([0.02, 0.02, 0.02], alpha=0.05) == [False, False, False]

    mixed = holm_bonferroni([0.001, 0.02, 0.02], alpha=0.05)
    assert mixed[1] == mixed[2], "tied p-values were given different decisions"


def test_holm_on_empty_input() -> None:
    assert holm_bonferroni([], alpha=0.05) == []
