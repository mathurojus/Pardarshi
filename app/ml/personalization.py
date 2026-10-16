"""Is the price different for different personas, beyond ordinary price noise?

Model: log(price) = b0 + b_country + b_device + b_user_state + noise   (ordinary least squares)

* Each persona differs from the baseline in ONE factor, so every coefficient is a clean
  'effect of this factor'. Repeats of each persona give the residual variance, which is the
  site's own noise level (dynamic pricing, A/B tests). A factor is only flagged when its
  effect clears that noise (p < 0.05 after Benjamini-Hochberg correction) AND is big enough
  to matter (default >= 2%).
* effect_pct = exp(b) - 1, with a 95% confidence interval.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from scipy import stats

from app.capture.schema import RunRecord

FACTORS = ("country", "device", "user_state")


def _benjamini_hochberg(p: list[float]) -> list[float]:
    n = len(p)
    if n == 0:
        return []
    order = np.argsort(p)
    ranked = np.array(p)[order] * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(adj, 0, 1)
    return out.tolist()


def analyse(
    runs: list[RunRecord],
    price_field: str = "final_price",
    alpha: float = 0.05,
    min_effect: float = 0.02,
) -> dict:
    rows = [(r.persona, getattr(r, price_field)) for r in runs if getattr(r, price_field)]
    base = {f: Counter(getattr(p, f) for p, _ in rows).most_common(1)[0][0] for f in FACTORS} if rows else {}
    result: dict = {"price_field": price_field, "n_runs": len(rows), "baseline": base, "effects": [],
                    "any_significant": False, "noise_pct": None, "note": ""}
    if len(rows) < 4:
        result["note"] = "Not enough completed runs for a statistical test."
        return result

    columns: list[tuple[str, str]] = []  # (factor, level)
    for f in FACTORS:
        for lv in sorted({getattr(p, f) for p, _ in rows}):
            if lv != base[f]:
                columns.append((f, lv))
    X = np.array([[1.0] + [1.0 if getattr(p, f) == lv else 0.0 for f, lv in columns] for p, _ in rows])
    y = np.log(np.array([float(v) for _, v in rows]))
    n, k = X.shape
    df = n - np.linalg.matrix_rank(X)
    if df <= 0:
        result["note"] = "Too few repeats per persona: no degrees of freedom left to estimate noise."
        return result

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sigma2 = float(resid @ resid) / df
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    tcrit = stats.t.ppf(1 - alpha / 2, df)

    effects, pvals = [], []
    for i, (f, lv) in enumerate(columns, start=1):
        b, s = float(beta[i]), float(se[i])
        if s < 1e-12:
            p = 0.0 if abs(b) > 1e-9 else 1.0
        else:
            p = float(2 * stats.t.sf(abs(b / s), df))
        pvals.append(p)
        effects.append({
            "factor": f, "level": lv, "vs": base[f],
            "effect_pct": round((np.exp(b) - 1) * 100, 2),
            "ci_low_pct": round((np.exp(b - tcrit * s) - 1) * 100, 2),
            "ci_high_pct": round((np.exp(b + tcrit * s) - 1) * 100, 2),
            "p": round(p, 5),
        })
    for e, pa in zip(effects, _benjamini_hochberg(pvals)):
        e["p_adj"] = round(pa, 5)
        e["significant"] = bool(pa < alpha and abs(e["effect_pct"]) >= min_effect * 100)
    result["effects"] = effects
    result["any_significant"] = any(e["significant"] for e in effects)
    result["noise_pct"] = round(float(np.sqrt(sigma2)) * 100, 2)
    result["df"] = int(df)
    result["note"] = (
        f"Residual noise (1 sd) is {result['noise_pct']}%. Effects are flagged only if p_adj < {alpha} "
        f"and |effect| >= {int(min_effect * 100)}%."
    )
    return result
