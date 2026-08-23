"""
THE FULL GRID — every strategy, mode, reward target and instrument
==================================================================

Entry decisions are computed ONCE per (strategy, mode, instrument) and the
three reward targets are then simulated from the same signals. That is a
threefold saving and it is also the cleaner comparison: the R:R rows differ
only in how the trade was managed, not in which trades were taken.

The simplification is worth stating: in the live bot a very close opposing
swing can downgrade an ENTRY to WAIT, and that check uses the target
distance, so entries could in principle differ slightly between 1:1 and 1:3.
Holding entries fixed removes that second-order effect in exchange for a
comparison where the exit is the only variable.

WHAT THIS CANNOT MEASURE, AND WILL NOT PRETEND TO
--------------------------------------------------
swap / overnight financing   the data feed carries none. Material for swing
                             positions held days; near-irrelevant for scalps.
bid/ask separately           OHLC mid prices only. Spread is modelled as a
                             cost, not as two price series.
leverage                     an account setting, not a backtest output. Sizing
                             here is a fixed fraction of equity at risk.
real fills                   no order book. Slippage is an assumption, applied
                             as half a spread against every entry.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

import numpy as np
import pandas as pd


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
from indicators import resample_ohlc
from strategies import REGISTRY

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RR_SETS = {"1:1": (1.0,), "1:2": (1.0, 2.0), "1:3": (1.0, 2.0, 3.0)}
RESAMPLE = {"5min": "5min", "15min": "15min", "30min": "30min", "1h": "1h",
            "2h": "2h", "4h": "4h", "1day": "1D", "1week": "1W"}
WARMUP, WINDOW = 300, 500
START_BALANCE = 10_000.0
RISK_PCT = 0.5                      # the brief's primary test


def collect_entries(df, mode, key, inst, max_bars=None):
    """Walk the series once, recording every ENTRY the strategy issues."""
    spec = C.MODES[mode]
    if max_bars:
        df = df.iloc[-max_bars:]
    trend = resample_ohlc(df, RESAMPLE[spec.trend_tf])
    bias = resample_ohlc(df, RESAMPLE[spec.bias_tf])
    t_idx, b_idx = trend.index, bias.index
    fn = REGISTRY[key].evaluate

    out = []
    for i in range(WARMUP, len(df)):
        now = df.index[i]
        te = t_idx.searchsorted(now, side="right")
        be = b_idx.searchsorted(now, side="right")
        if te < 60 or be < 60:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            res = fn(df.iloc[max(0, i - WINDOW):i + 1],
                     trend.iloc[max(0, te - 300):te],
                     bias.iloc[max(0, be - 300):be],
                     spec, now.to_pydatetime(), instrument=inst)
        if res.get("decision") != "ENTRY" or not res.get("levels"):
            continue
        lv = res["levels"]
        out.append({
            "i": i, "time": now, "dir": res["direction"],
            "entry": lv["entry"], "risk": abs(lv["entry"] - lv["stop"]),
            "atr": lv["atr"], "score": res.get("score"),
            "confidence": (res.get("confidence") or {}).get("value"),
            "time_exit": res.get("time_exit_bars"),
            "session": _session_of(now),
            "reason": "; ".join(r["text"] for r in res.get("reasons", [])
                                if r["ok"])[:90],
        })
    return df, out


def _session_of(ts):
    h = ts.hour
    if 12 <= h < 21: return "NY"
    if 7 <= h < 16: return "London"
    if h >= 23 or h < 8: return "Asia"
    return "Sydney"


def simulate_exits(df, entries, tps, inst, cost_r_fn, max_hold=200):
    """Replay recorded entries with a given target ladder."""
    h = df["high"].to_numpy(float); l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    k = len(tps)
    trades = []
    busy_until = -1
    for e in entries:
        if e["i"] <= busy_until:
            continue                                   # one position at a time
        d, entry, risk = e["dir"], e["entry"], e["risk"]
        if risk <= 0:
            continue
        hold = e["time_exit"] or max_hold
        targets = [entry + d * risk * m for m in tps]
        stop = entry - d * risk
        cur_stop, hit = stop, 0
        reason, i_exit = "timeout", min(e["i"] + hold, len(df) - 1)
        for t in range(e["i"] + 1, min(e["i"] + 1 + hold, len(df))):
            stopped = (l[t] <= cur_stop) if d > 0 else (h[t] >= cur_stop)
            reached = [m for m in range(hit, k)
                       if (h[t] >= targets[m] if d > 0 else l[t] <= targets[m])]
            if stopped:
                reason, i_exit = ("stop" if hit == 0 else "breakeven"), t
                break
            if reached:
                hit = max(reached) + 1
                if hit == 1 and C.MOVE_TO_BREAKEVEN_AFTER_TP1:
                    cur_stop = entry
                if hit >= k:
                    reason, i_exit = "target", t
                    break
        cost = cost_r_fn(risk)
        if reason == "timeout":
            banked = sum(tps[:hit]) / k if hit else 0.0
            rem = (k - hit) / k
            r_gross = banked + rem * d * (c[i_exit] - entry) / risk
        else:
            r_gross = -1.0 if hit == 0 else sum(tps[:hit]) / k
        exit_px = (cur_stop if reason in ("stop", "breakeven")
                   else targets[-1] if reason == "target" else c[i_exit])
        trades.append({**e, "exit_i": i_exit, "exit_time": df.index[i_exit],
                       "exit": exit_px, "targets_hit": hit,
                       "r_gross": r_gross, "r": r_gross - cost,
                       "cost_r": cost, "bars": i_exit - e["i"],
                       "exit_reason": reason,
                       "tp_shown": targets[0] if targets else None})
        busy_until = i_exit
    return trades


# --------------------------------------------------------------------------- #
#  Reporting
# --------------------------------------------------------------------------- #
def summarise(trades, inst, tf, label):
    """Every figure the brief asks for that this data can honestly support."""
    if not trades:
        return {"combo": label, "trades": 0}
    r = np.array([t["r"] for t in trades])
    rg = np.array([t["r_gross"] for t in trades])
    risk_cash = START_BALANCE * RISK_PCT / 100.0
    pnl = r * risk_cash

    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = eq - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    # Longest stretch under water, in trades
    under, longest, cur = dd < -1e-9, 0, 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)

    streak = best_w = best_l = 0
    for x in r:
        if x > 0:
            streak = streak + 1 if streak > 0 else 1
            best_w = max(best_w, streak)
        else:
            streak = streak - 1 if streak < 0 else -1
            best_l = min(best_l, streak)

    times = pd.to_datetime([t["time"] for t in trades])
    span_days = max((times[-1] - times[0]).days, 1)
    monthly = pd.Series(pnl, index=times).resample("ME").sum()
    yearly = pd.Series(pnl, index=times).resample("YE").sum()

    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    dn = r[r < 0].std(ddof=1) if (r < 0).sum() > 1 else 0.0
    bars_s = TF_SEC.get(tf, 900)

    return {
        "combo": label, "trades": len(r),
        "wins": int((r > 0).sum()), "losses": int((r <= 0).sum()),
        "win_rate": float((r > 0).mean()),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "rr_realised": float(abs(wins.mean() / losses.mean()))
                       if len(wins) and len(losses) and losses.mean() else np.nan,
        "expectancy_r": float(r.mean()),
        "expectancy_gross_r": float(rg.mean()),
        "expectancy_usd": float(pnl.mean()),
        "total_r": float(r.sum()), "net_usd": float(pnl.sum()),
        "gross_profit_usd": float(pnl[pnl > 0].sum()),
        "gross_loss_usd": float(pnl[pnl <= 0].sum()),
        "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl <= 0].sum())
                         if (pnl <= 0).any() and pnl[pnl <= 0].sum() < 0
                         else float("inf"),
        "max_dd_usd": max_dd,
        "max_dd_pct": 100.0 * max_dd / START_BALANCE,
        "recovery": float(pnl.sum() / abs(max_dd)) if max_dd < 0 else float("inf"),
        "sharpe": float(r.mean() / sd * np.sqrt(len(r))) if sd > 0 else 0.0,
        "sortino": float(r.mean() / dn * np.sqrt(len(r))) if dn > 0 else 0.0,
        "start_balance": START_BALANCE,
        "end_balance": START_BALANCE + float(pnl.sum()),
        "return_pct": 100.0 * float(pnl.sum()) / START_BALANCE,
        "best_trade_usd": float(pnl.max()), "worst_trade_usd": float(pnl.min()),
        "max_win_streak": int(best_w), "max_loss_streak": int(abs(best_l)),
        "longest_dd_trades": int(longest),
        "period": f"{times[0]:%Y-%m-%d} to {times[-1]:%Y-%m-%d}",
        "best_month_usd": float(monthly.max()) if len(monthly) else 0.0,
        "worst_month_usd": float(monthly.min()) if len(monthly) else 0.0,
        "avg_month_usd": float(monthly.mean()) if len(monthly) else 0.0,
        "best_year_usd": float(yearly.max()) if len(yearly) else 0.0,
        "worst_year_usd": float(yearly.min()) if len(yearly) else 0.0,
        "trades_per_day": len(r) / span_days,
        "trades_per_week": len(r) / span_days * 7,
        "trades_per_month": len(r) / span_days * 30.4,
        "avg_duration_min": float(np.mean([t["bars"] for t in trades]) * bars_s / 60),
        "avg_cost_r": float(np.mean([t["cost_r"] for t in trades])),
        "top_trade_share": float(r.max() / r.sum()) if r.sum() > 0 else np.nan,
    }


TF_SEC = {"5min": 300, "15min": 900, "1h": 3600, "4h": 14400}


# --------------------------------------------------------------------------- #
#  Driver
# --------------------------------------------------------------------------- #
MODE_TF = {"scalp": "5min", "intraday": "15min", "swing": "4h"}
CAP = {"5min": 40000, "15min": 40000, "4h": 20000}


def load(pair, tf):
    d = pd.read_csv(f"data/{pair}_{tf}.csv", parse_dates=["datetime"])
    return d.set_index("datetime")[["open", "high", "low", "close"]]


def one_combo(args):
    pair, mode, key = args
    import instruments as _I
    tf = MODE_TF[mode]
    inst = _I.find(pair)
    path = f"data/{pair}_{tf}.csv"
    if not os.path.exists(path) or inst is None:
        return []
    df = load(pair, tf)
    if len(df) < 1500:
        return []
    df, entries = collect_entries(df, mode, key, inst, max_bars=CAP[tf])
    if not entries:
        return [{"combo": f"{pair}|{mode}|{key}|-", "trades": 0,
                 "pair": pair.upper(), "mode": mode,
                 "strategy": REGISTRY[key].name, "rr": "-"}]

    spread = inst.spread
    slip = spread * 0.5
    cost_r = lambda risk: (spread + slip) / risk if risk > 0 else 0.0

    rows, tradelog = [], []
    # A time-exit strategy has no target ladder; running three is meaningless.
    rr_items = ([("clock", (99.0,))] if entries[0].get("time_exit")
                else list(RR_SETS.items()))
    for rr, tps in rr_items:
        tr = simulate_exits(df, entries, tps, inst, cost_r)
        s = summarise(tr, inst, tf, f"{pair}|{mode}|{key}|{rr}")
        s.update(pair=pair.upper(), mode=mode, strategy=REGISTRY[key].name,
                 rr=rr, spread=spread, slippage=slip, commission=0.0,
                 swap="not modelled")
        rows.append(s)
        for n, t in enumerate(tr, 1):
            tradelog.append({
                "n": n, "pair": pair.upper(), "strategy": REGISTRY[key].name,
                "mode": mode, "rr": rr, "time": t["time"],
                "dir": "BUY" if t["dir"] > 0 else "SELL",
                "entry": t["entry"], "stop": round(t["entry"] - t["dir"]*t["risk"], 5),
                "tp1": t["tp_shown"], "exit": round(t["exit"], 5),
                "exit_time": t["exit_time"], "risk_usd": START_BALANCE*RISK_PCT/100,
                "pnl_usd": round(t["r"] * START_BALANCE*RISK_PCT/100, 2),
                "r": round(t["r"], 4), "r_gross": round(t["r_gross"], 4),
                "cost_r": round(t["cost_r"], 4), "spread": spread,
                "slippage": slip, "bars": t["bars"], "session": t["session"],
                "score": t["score"], "confidence": t["confidence"],
                "exit_reason": t["exit_reason"], "entry_reason": t["reason"],
            })
    if tradelog:
        os.makedirs("research/out", exist_ok=True)
        pd.DataFrame(tradelog).to_csv(
            f"research/out/trades_{pair}_{mode}_{key}.csv", index=False)
    return rows


def main():
    import concurrent.futures as cf
    pairs = ("xauusd", "eurusd", "gbpusd", "usdjpy")
    jobs = [(p, m, k) for p in pairs for m in MODE_TF for k in REGISTRY]
    print(f"{len(jobs)} combinations "
          f"({len(pairs)} pairs x {len(MODE_TF)} modes x {len(REGISTRY)} strategies)")
    print(f"risk {RISK_PCT}% of ${START_BALANCE:,.0f} per trade, "
          f"spread + half-spread slippage, no swap\n")
    rows = []
    workers = max(1, (os.cpu_count() or 2) - 1)
    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one_combo, j): j for j in jobs}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            try:
                got = fut.result()
            except Exception as exc:
                print(f"  [{n}/{len(jobs)}] {futs[fut]} FAILED {type(exc).__name__}: {exc}")
                continue
            rows += got
            j = futs[fut]
            tot = sum(r.get("trades", 0) for r in got)
            print(f"  [{n}/{len(jobs)}] {j[0]:<7}{j[1]:<9}{j[2]:<9} {tot:>5} trades",
                  flush=True)
    d = pd.DataFrame(rows)
    os.makedirs("research/out", exist_ok=True)
    d.to_csv("research/out/grid_summary.csv", index=False)
    print(f"\nwrote research/out/grid_summary.csv  ({len(d)} rows)")
    return d


if __name__ == "__main__":
    main()
