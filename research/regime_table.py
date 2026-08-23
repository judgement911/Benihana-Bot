"""
MEASURED EXPECTANCY PER MARKET CONDITION
========================================

All-in-One needs to answer one question: given the market as it is right
now, which strategy has actually made money in conditions like these?

Not "which is most confident" — that test failed badly, with three of five
strategies showing LOWER expectancy in their top confidence bucket than
their middle one. A score nobody validated is not evidence.

So this reads the trade logs and buckets every trade by the conditions it
was taken in — trend strength and volatility, both recorded at entry — then
reports expectancy and trade count per bucket. That table is the lookup.

A bucket only counts as usable with MIN_TRADES behind it. Expectancy from
forty trades is a rumour.
"""
from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

MIN_TRADES = 100
MIN_EXPECTANCY = 0.05          # below this, not worth the risk of being wrong

ADX_BANDS = [(0, 20, "quiet"), (20, 30, "trending"), (30, 999, "strong")]
VOL_BANDS = [(0, 0.9, "low"), (0.9, 1.3, "normal"), (1.3, 99, "high")]


def band(value, bands, default="unknown"):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    for lo, hi, name in bands:
        if lo <= value < hi:
            return name
    return default


def build(out_dir="research/out") -> pd.DataFrame:
    rows = []
    for path in sorted(glob.glob(f"{out_dir}/trades_*.csv")):
        d = pd.read_csv(path)
        if d.empty:
            continue
        d["adx_band"] = d["adx"].apply(lambda v: band(v, ADX_BANDS))
        d["vol_band"] = d["vol_ratio"].apply(lambda v: band(v, VOL_BANDS))
        for (strat, mode, rr, a, v), g in d.groupby(
                ["strategy", "mode", "rr", "adx_band", "vol_band"]):
            if len(g) < 20:
                continue
            r = g["r"].to_numpy()
            se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
            rows.append({
                "strategy": strat, "mode": mode, "rr": rr,
                "adx_band": a, "vol_band": v, "trades": len(r),
                "expectancy": float(r.mean()),
                "win_rate": float((r > 0).mean()),
                "t": float(r.mean() / se) if se and se > 0 else 0.0,
                "usable": bool(len(r) >= MIN_TRADES and r.mean() >= MIN_EXPECTANCY),
            })
    return pd.DataFrame(rows)


def to_lookup(df: pd.DataFrame) -> dict:
    """Flatten to the JSON the bot reads at signal time."""
    out = {}
    for _, r in df.iterrows():
        key = f"{r.adx_band}|{r.vol_band}"
        out.setdefault(key, []).append({
            "strategy": r.strategy, "mode": r["mode"], "rr": r.rr,
            "expectancy": round(float(r.expectancy), 4),
            "trades": int(r.trades), "t": round(float(r.t), 2),
            "usable": bool(r.usable),
        })
    for key in out:
        out[key].sort(key=lambda x: -x["expectancy"])
    return out


if __name__ == "__main__":
    d = build()
    if d.empty:
        raise SystemExit("No trade logs found. Run fullgrid.py first.")
    d.to_csv("research/out/regime_table.csv", index=False)

    usable = d[d.usable]
    print(f"{len(d)} strategy/mode/regime buckets, "
          f"{len(usable)} usable (>= {MIN_TRADES} trades and >= "
          f"{MIN_EXPECTANCY:+.2f}R)\n")
    print(f"{'regime':<20}{'strategy':<15}{'mode':<10}{'R:R':<7}"
          f"{'n':>6}{'exp':>8}{'t':>7}")
    print("-" * 73)
    for key, items in sorted(to_lookup(usable).items()):
        for it in items:
            print(f"{key:<20}{it['strategy']:<15}{it['mode']:<10}{it['rr']:<7}"
                  f"{it['trades']:>6}{it['expectancy']:>+8.3f}{it['t']:>+7.2f}")
    if usable.empty:
        print("  NOTHING QUALIFIES. No strategy has a demonstrated edge in any")
        print("  regime at the required trade count and expectancy.")

    lookup = to_lookup(d)
    with open("research/out/regime_lookup.json", "w") as fh:
        json.dump(lookup, fh, indent=1)
    print("\nwrote research/out/regime_lookup.json")
