"""Shared harness for the scalp investigation. Every experiment measures the
same way so results can be compared rather than argued about."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import Costs, simulate
from features import build
import split as SP

FLAT = 0


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(pair="xauusd", tf="5min"):
    """Absolute path: agents run from wherever, and a relative one silently
    resolves against the caller's cwd."""
    d = pd.read_csv(os.path.join(ROOT, "data", f"{pair}_{tf}.csv"),
                    parse_dates=["datetime"])
    return d.set_index("datetime")[["open", "high", "low", "close"]]


def prep(pair="xauusd", tf="5min", cap=None):
    """cap trims to the LAST n bars. Use it to speed up a scan, never to
    evaluate a rule — see the note in scalp_validate.gauntlet."""
    df = load(pair, tf)
    if cap:
        df = df.iloc[-cap:]
    f = build(df)
    ok = f["atr"].notna() & f["z_20"].notna()
    return df[ok], f[ok]


def run(df, f, sig, stop_pts, inst, tps=(1.0,), max_bars=48,
        commission_r=0.0, slip_mult=0.5):
    """One backtest. Signal and stop must already be causal (decided at bar i,
    filled at i+1) — simulate() applies the offset."""
    costs = Costs.for_instrument(inst, slip_mult=slip_mult,
                                 commission_r=commission_r)
    return simulate(df, np.asarray(sig, float), np.asarray(stop_pts, float),
                    tps, costs, max_bars=max_bars, entry_offset=1)


def stats(res, label=""):
    r = res.r_series(net=True)
    g = res.r_series(net=False)
    n = len(r)
    if n == 0:
        return {"label": label, "n": 0, "net": 0.0, "gross": 0.0, "t": 0.0,
                "win": 0.0, "cost": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0}
    sd = r.std(ddof=1) if n > 1 else 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {
        "label": label, "n": n,
        "net": float(r.mean()), "gross": float(g.mean()),
        "cost": float(g.mean() - r.mean()),
        "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0,
        "win": float((r > 0).mean()),
        "sharpe": float(r.mean() / sd) if sd > 0 else 0.0,
        "maxdd": float((eq - peak).min()),
        "pf": float(wins / losses) if losses > 0 else float("inf"),
        "total": float(r.sum()),
    }


def fmt(rows, title=""):
    out = []
    if title:
        out.append(title)
    out.append(f"  {'variant':<34}{'n':>6}{'win':>6}{'gross':>8}{'cost':>7}"
               f"{'net':>8}{'t':>7}{'PF':>6}{'totR':>8}")
    for s in rows:
        out.append(f"  {s['label']:<34}{s['n']:>6}{s['win']:>6.0%}"
                   f"{s['gross']:>+8.3f}{s['cost']:>7.3f}{s['net']:>+8.3f}"
                   f"{s['t']:>+7.2f}{s['pf']:>6.2f}{s['total']:>+8.1f}")
    return "\n".join(out)


def parts(df, f):
    """IS / VAL / OOS index masks, chronological."""
    b = SP.bounds(len(df))
    return {k: (lo, hi) for k, (lo, hi) in b.items()}
