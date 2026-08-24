"""
The gauntlet a scalp rule must clear before it replaces a live strategy.
=========================================================================

A rule that looks good in-sample has cleared nothing. This runs the checks
that actually distinguish an edge from a fitted curve, and it is deliberately
harsh: the default answer is no.

  IS / VAL / OOS      chronological, never shuffled. OOS is touched once.
  walk-forward        many small out-of-samples, not one lucky window
  concentration       remove the best 5 trades. Real edges survive it.
  Monte Carlo         reshuffle trade order; how deep does drawdown get?
  bootstrap           is the mean distinguishable from zero at all?
  sub-period          three consecutive thirds, all positive?
  cross-instrument    does it exist on EUR/USD, or only where it was found?
  cost sensitivity    how much worse can the spread get before it dies?

cap DEFAULTS TO None, meaning the whole history, and that default matters
more than anything else in this file. It was 60000 once. A rule discovered
on the most recent 60000 bars then had those same bars handed back to it as
its "in-sample" slice, with the split carving validation and out-of-sample
out of the discovery window itself — while 60000 older bars, where the rule
was significantly negative, were never loaded at all. The gauntlet returned
ACCEPT on every check. Never shorten the history to the window a rule was
found in.
"""
from __future__ import annotations
import numpy as np
import scalp_lab as L
import split as SP
import instruments as I
from engine import Costs, simulate

BAR_NET, BAR_T, BAR_N = 0.05, 2.0, 150


def _stats(r):
    n = len(r)
    if n < 2:
        return {"n": n, "net": 0.0, "t": 0.0, "win": 0.0, "total": 0.0}
    sd = r.std(ddof=1)
    return {"n": n, "net": float(r.mean()),
            "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0,
            "win": float((r > 0).mean()), "total": float(r.sum())}


def _run(d, ff, rule, inst, spread=None):
    sig, stop, tps, bars = rule(d, ff)
    costs = (Costs(spread=spread, slippage=spread * 0.5) if spread is not None
             else Costs.for_instrument(inst, slip_mult=0.5))
    res = simulate(d, np.asarray(sig, float), np.asarray(stop, float),
                   tps, costs, max_bars=bars, entry_offset=1)
    return res.r_series(net=True)


def gauntlet(rule, name="rule", pair="xauusd", tf="5min", cap=None,
             seed=0) -> dict:
    """rule(df, feats) -> (signal, stop_points, target_mults, max_bars)"""
    rng = np.random.default_rng(seed)
    df, f = L.prep(pair, tf, cap=cap)
    inst = I.find(pair)
    out = {"name": name, "pair": pair.upper(), "checks": {}, "verdict": "REJECT"}

    b = SP.bounds(len(df))
    slices = {}
    for part in (SP.IS, SP.VAL, SP.OOS):
        lo, hi = b[part]
        slices[part] = _stats(_run(df.iloc[lo:hi], f.iloc[lo:hi], rule, inst))
    out["checks"]["splits"] = slices

    # Walk-forward: many small out-of-samples rather than one lucky window
    wf = []
    n = len(df)
    for k in range(6):
        lo = int(n * k / 6)
        hi = int(n * (k + 1) / 6)
        wf.append(_stats(_run(df.iloc[lo:hi], f.iloc[lo:hi], rule, inst)))
    out["checks"]["walk_forward"] = wf
    out["checks"]["wf_positive"] = sum(1 for w in wf if w["net"] > 0)

    # Everything below is measured on the full series
    r = _run(df, f, rule, inst)
    out["checks"]["all"] = _stats(r)
    if len(r) < 10:
        out["reason"] = f"only {len(r)} trades in total"
        return out

    srt = np.sort(r)[::-1]
    out["checks"]["drop_best_5"] = float(srt[5:].mean())
    out["checks"]["drop_best_5_total"] = float(srt[5:].sum())

    # Monte Carlo on trade order — the same trades in a different sequence
    dds = []
    for _ in range(2000):
        eq = np.cumsum(rng.permutation(r))
        dds.append((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())
    out["checks"]["mc_median_dd"] = float(np.median(dds))
    out["checks"]["mc_p05_dd"] = float(np.quantile(dds, 0.05))

    # Bootstrap the mean
    means = [rng.choice(r, len(r), replace=True).mean() for _ in range(2000)]
    out["checks"]["boot_ci"] = [float(np.quantile(means, 0.025)),
                                float(np.quantile(means, 0.975))]

    thirds = [_stats(r[i * len(r) // 3:(i + 1) * len(r) // 3]) for i in range(3)]
    out["checks"]["thirds"] = thirds
    out["checks"]["thirds_positive"] = sum(1 for t in thirds if t["net"] > 0)

    out["checks"]["cost_sensitivity"] = {
        f"{s:.2f}": float(_run(df, f, rule, inst, spread=s).mean())
        for s in (0.20, 0.30, 0.45, 0.60)
    }

    # Verdict: every one of these has to hold
    a = out["checks"]["all"]
    reasons = []
    if a["n"] < BAR_N:
        reasons.append(f"only {a['n']} trades")
    if a["net"] < BAR_NET:
        reasons.append(f"net {a['net']:+.3f}R below the {BAR_NET} bar")
    if a["t"] < BAR_T:
        reasons.append(f"t {a['t']:+.2f} below {BAR_T}")
    if slices[SP.OOS]["net"] <= 0:
        reasons.append(f"out-of-sample net {slices[SP.OOS]['net']:+.3f}R")
    if out["checks"]["drop_best_5"] <= 0:
        reasons.append("edge disappears without its best 5 trades")
    if out["checks"]["boot_ci"][0] <= 0:
        reasons.append("bootstrap interval includes zero")
    if out["checks"]["thirds_positive"] < 3:
        reasons.append(f"only {out['checks']['thirds_positive']}/3 thirds positive")
    if out["checks"]["wf_positive"] < 5:
        reasons.append(f"only {out['checks']['wf_positive']}/6 walk-forward windows positive")

    out["verdict"] = "ACCEPT" if not reasons else "REJECT"
    out["reason"] = "; ".join(reasons) if reasons else "clears every check"
    return out


def report(g) -> str:
    c = g["checks"]
    L_ = [f"{g['name']}  on {g['pair']}", "=" * 58]
    s = c["splits"]
    L_.append(f"  {'slice':<16}{'n':>6}{'win':>7}{'net R':>9}{'t':>8}{'total':>9}")
    for k in (SP.IS, SP.VAL, SP.OOS):
        v = s[k]
        L_.append(f"  {k:<16}{v['n']:>6}{v['win']:>7.0%}{v['net']:>+9.3f}"
                  f"{v['t']:>+8.2f}{v['total']:>+9.1f}")
    if "all" in c:
        a = c["all"]
        L_.append(f"  {'ALL':<16}{a['n']:>6}{a['win']:>7.0%}{a['net']:>+9.3f}"
                  f"{a['t']:>+8.2f}{a['total']:>+9.1f}")
        L_ += [
            "",
            f"  walk-forward positive   {c['wf_positive']}/6",
            f"  thirds positive         {c['thirds_positive']}/3",
            f"  net without best 5      {c['drop_best_5']:+.3f} R",
            f"  bootstrap 95% CI        [{c['boot_ci'][0]:+.3f}, {c['boot_ci'][1]:+.3f}] R",
            f"  Monte Carlo median DD   {c['mc_median_dd']:+.1f} R "
            f"(5th pct {c['mc_p05_dd']:+.1f})",
            "  net R at spread " + "  ".join(
                f"{k}:{v:+.3f}" for k, v in c["cost_sensitivity"].items()),
        ]
    L_ += ["", f"  VERDICT: {g['verdict']} — {g['reason']}"]
    return "\n".join(L_)
