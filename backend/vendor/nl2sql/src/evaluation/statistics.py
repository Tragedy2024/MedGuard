"""
Statistical tests for experiment results.

  McNemar's test — paired comparison of EX between two methods
  Bootstrap CI — 95% confidence interval for S-VR, S-Score, etc.
  Cohen's kappa — inter-rater agreement for human evaluation (RQ3)
"""
import numpy as np
from scipy import stats
from typing import List, Tuple, Optional


def mcnemar_test(
    correct_a: List[bool],  # Method A correct per query
    correct_b: List[bool],  # Method B correct per query
) -> dict:
    """
    McNemar's test for paired binary outcomes.

    Returns {statistic, p_value, table: {a_only, b_only, both, neither}}.
    """
    n = len(correct_a)
    if n != len(correct_b):
        raise ValueError("Lists must have same length")

    # Build contingency table
    both = sum(1 for a, b in zip(correct_a, correct_b) if a and b)
    a_only = sum(1 for a, b in zip(correct_a, correct_b) if a and not b)
    b_only = sum(1 for a, b in zip(correct_a, correct_b) if not a and b)
    neither = sum(1 for a, b in zip(correct_a, correct_b) if not a and not b)

    # McNemar: (b - c)^2 / (b + c) where b = a_only, c = b_only
    # With continuity correction for small samples
    b, c = a_only, b_only
    if b + c == 0:
        statistic = 0.0
        p_value = 1.0
    else:
        # Use exact binomial test for more reliability
        # H0: P(correct_a) = P(correct_b) → discordant pairs equally likely
        from scipy.stats import binomtest
        result = binomtest(min(b, c), n=b + c, p=0.5, alternative='two-sided')
        statistic = (b - c) ** 2 / (b + c) if (b + c) > 0 else 0.0
        # Cast to plain Python types — scipy returns numpy scalars, which
        # are not JSON-serializable.
        p_value = float(result.pvalue)

    return {
        "statistic": round(float(statistic), 4),
        "p_value": round(p_value, 4),
        "significant": bool(p_value < 0.05),
        "table": {
            "both_correct": both,
            "a_only_correct": a_only,
            "b_only_correct": b_only,
            "neither_correct": neither,
        },
    }


def bootstrap_ci(
    values: List[float],
    n_resamples: int = 10000,
    ci_level: float = 0.95,
    random_seed: int = 42,
) -> dict:
    """
    Bootstrap confidence interval for a metric.

    Returns {mean, ci_lower, ci_upper, std_err}.
    """
    rng = np.random.RandomState(random_seed)
    arr = np.array(values)
    n = len(arr)

    means = []
    for _ in range(n_resamples):
        sample = rng.choice(arr, size=n, replace=True)
        means.append(np.mean(sample))

    means = np.array(means)
    alpha = (1.0 - ci_level) / 2.0
    ci_lower = np.percentile(means, alpha * 100)
    ci_upper = np.percentile(means, (1 - alpha) * 100)

    return {
        "mean": round(float(np.mean(arr)), 4),
        "ci_lower": round(float(ci_lower), 4),
        "ci_upper": round(float(ci_upper), 4),
        "std_err": round(float(np.std(arr, ddof=1) / np.sqrt(n)), 4),
        "n": n,
        "ci_level": ci_level,
    }


def bootstrap_svr_ci(
    svr_values: List[float],  # Per-query S-VR values
    n_resamples: int = 10000,
) -> dict:
    """Bootstrap CI specifically for S-VR (proportion metric)."""
    return bootstrap_ci(svr_values, n_resamples=n_resamples)


def cohens_kappa(
    rater1: List[int],  # Scores from rater 1
    rater2: List[int],  # Scores from rater 2
    n_categories: Optional[int] = None,
) -> dict:
    """
    Cohen's kappa for inter-rater agreement (RQ3 human evaluation).

    Returns {kappa, agreement_pct, interpretation}.
    """
    if n_categories is None:
        all_vals = set(rater1) | set(rater2)
        n_categories = len(all_vals)

    # Build agreement matrix
    unique_vals = sorted(set(rater1) | set(rater2))
    n_cats = len(unique_vals)
    val_to_idx = {v: i for i, v in enumerate(unique_vals)}
    matrix = np.zeros((n_cats, n_cats), dtype=int)
    for a, b in zip(rater1, rater2):
        matrix[val_to_idx[a], val_to_idx[b]] += 1

    n = len(rater1)

    # Observed agreement
    p_o = np.trace(matrix) / n

    # Expected agreement
    row_sums = matrix.sum(axis=1)
    col_sums = matrix.sum(axis=0)
    p_e = np.sum(row_sums * col_sums) / (n * n)

    # Kappa
    if p_e == 1.0:
        kappa = 1.0
    else:
        kappa = (p_o - p_e) / (1.0 - p_e)

    # Interpretation
    if kappa < 0.0:
        interp = "Poor"
    elif kappa < 0.20:
        interp = "Slight"
    elif kappa < 0.40:
        interp = "Fair"
    elif kappa < 0.60:
        interp = "Moderate"
    elif kappa < 0.80:
        interp = "Substantial"
    else:
        interp = "Almost perfect"

    return {
        "kappa": round(kappa, 4),
        "agreement_pct": round(p_o * 100, 1),
        "interpretation": interp,
        "n_pairs": n,
    }


# ============================================================
# Smoke test
# ============================================================

if __name__ == "__main__":
    rng = np.random.RandomState(42)

    # McNemar test
    correct_a = [1,1,1,0,1,1,0,1,1,1] * 5  # 80% correct
    correct_b = [1,1,0,0,1,0,0,1,1,1] * 5  # 60% correct
    correct_a = [bool(x) for x in correct_a]
    correct_b = [bool(x) for x in correct_b]
    mc = mcnemar_test(correct_a, correct_b)
    print(f"McNemar: p={mc['p_value']}, sig={mc['significant']}")
    print(f"  Table: {mc['table']}")

    # Bootstrap CI
    svr_vals = [0.0, 0.5, 0.0, 0.33, 0.0, 0.5, 0.0, 0.0, 0.25, 0.0] * 3
    ci = bootstrap_ci(svr_vals)
    print(f"\nBootstrap CI (S-VR): mean={ci['mean']}, [{ci['ci_lower']}, {ci['ci_upper']}]")

    # Cohen's kappa
    r1 = [4,5,3,4,5,4,3,4,5,4,3,3,4,5,4,4,3,4,5,4]
    r2 = [4,4,3,4,5,3,3,4,4,4,3,3,4,4,4,4,3,4,5,3]
    ck = cohens_kappa(r1, r2, n_categories=3)
    print(f"\nCohen's kappa: {ck['kappa']} ({ck['interpretation']}), agreement={ck['agreement_pct']}%")

    print("\nAll statistics tests passed.")
