"""
A FAST, HONEST BACKTEST ENGINE FOR RESEARCH
===========================================

The bot's backtester re-evaluates indicators bar by bar, which is right for
a live signal and hopeless for research: 25 seconds a run, and this mission
needs thousands of runs for parameter sweeps, walk-forward and Monte Carlo.

So features are computed once, vectorised, over the whole series, and the
simulator is a single tight pass. Same answers, ~1000x faster.

THE THREE WAYS A BACKTEST LIES, AND WHAT IS DONE ABOUT EACH
-----------------------------------------------------------
Look-ahead. Every feature is built from CLOSED bars and then shifted one
bar before it can produce a signal, so a decision at bar i can only see
information complete at bar i-1. Entry fills at bar i's OPEN. A feature
that peeks is the single easiest way to invent an edge that does not exist,
so the shift is applied centrally here rather than trusted to each strategy.

Optimistic fills. When a bar's range contains both the stop and a target,
this engine takes the STOP. From OHLC alone the order is unknowable, and
assuming the good one is how backtests learn to flatter themselves.

Free trading. Every trade pays spread plus slippage plus commission, in R,
deducted from the result. Gross and net are reported separately because
gross is the number that looks good and net is the number you live on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

LONG, SHORT, FLAT = 1, -1, 0


@dataclass
class Costs:
    """Dealing costs in PRICE units, converted to R per trade.

    This has to be per-trade, not a flat R figure, because the cost of a
    trade in R is spread divided by that trade's stop distance — and that is
    the entire scalping question. The same 0.30 spread on gold is 0.006R
    against a 50-point swing stop and 0.075R against a 4-point scalp stop:
    a rounding error in one case and a tenth of the risk budget in the other.
    Charging a flat R would hide exactly the effect being investigated.
    """
    spread: float = 0.0          # full spread in price units
    slippage: float = 0.0        # extra adverse fill, price units
    commission_r: float = 0.0    # broker fee, genuinely proportional to size

    def r_for(self, risk: float) -> float:
        if risk <= 0:
            return 0.0
        return (self.spread + self.slippage) / risk + self.commission_r

    @classmethod
    def for_instrument(cls, inst, slip_mult: float = 0.5, commission_r: float = 0.0):
        """Spread from the instrument, slippage as a fraction of it.

        Half a spread of slippage is a deliberately unkind default: retail
        fills on a stop order in a fast market are frequently worse.
        """
        return cls(spread=inst.spread, slippage=inst.spread * slip_mult,
                   commission_r=commission_r)


@dataclass
class Trade:
    i_entry: int
    i_exit: int
    direction: int
    entry: float
    stop: float
    targets: tuple
    targets_hit: int
    r_gross: float
    r_net: float
    bars_held: int
    exit_reason: str


@dataclass
class Result:
    trades: list = field(default_factory=list)
    equity: np.ndarray = None

    def r_series(self, net=True) -> np.ndarray:
        return np.array([(t.r_net if net else t.r_gross) for t in self.trades])


def simulate(df: pd.DataFrame, signal: np.ndarray, stop_dist: np.ndarray,
             target_mults: tuple, costs: Costs,
             max_bars: int = 200, entry_offset: int = 1) -> Result:
    """Walk the bars once, holding at most one position.

    signal[i]      the direction decided from information complete at bar i
    stop_dist[i]   the 1R distance for a trade decided at bar i
    entry_offset   bars between the decision and the fill; 1 = next bar's open

    Partial exits: equal slices to each target, and once the first fills the
    remainder rides at breakeven. This mirrors probability.realised_r exactly
    — the whole codebase has to price a trade the same way or the research
    measures a different strategy than the bot trades.
    """
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c_arr = df["close"].to_numpy(float)
    n = len(df)
    mults = np.asarray(target_mults, float)
    k = len(mults)

    trades: list[Trade] = []
    i = 0
    while i < n - entry_offset - 1:
        d = int(signal[i])
        if d == FLAT or not np.isfinite(stop_dist[i]) or stop_dist[i] <= 0:
            i += 1
            continue

        j = i + entry_offset                  # fill bar
        entry = o[j]
        risk = float(stop_dist[i])
        stop = entry - d * risk
        targets = entry + d * risk * mults

        hit = 0
        cur_stop = stop
        exit_reason, i_exit = "open", n - 1
        for t in range(j, min(j + max_bars, n)):
            stopped = (l[t] <= cur_stop) if d > 0 else (h[t] >= cur_stop)
            if d > 0:
                reached = [m for m in range(hit, k) if h[t] >= targets[m]]
            else:
                reached = [m for m in range(hit, k) if l[t] <= targets[m]]

            if stopped:                        # tie goes to the stop, always
                exit_reason = "stop" if hit == 0 else "breakeven"
                i_exit = t
                break
            if reached:
                hit = max(reached) + 1
                if hit == 1:
                    cur_stop = entry           # breakeven after the first fill
                if hit >= k:
                    exit_reason, i_exit = "target", t
                    break
        else:
            exit_reason, i_exit = "timeout", min(j + max_bars, n) - 1

        if exit_reason == "timeout":
            # A trade closed by the clock exits at the market, so it is worth
            # whatever it is worth — scoring it as a full stop-out was simply
            # wrong, and it makes any time-exit strategy untestable because
            # every one of its trades ends this way.
            r_gross = d * (c_arr[i_exit] - entry) / risk
            if hit:
                # Slices already banked keep their value; only the remainder
                # is marked to market.
                banked = float(mults[:hit].sum() / k)
                remaining = (k - hit) / k
                r_gross = banked + remaining * d * (c_arr[i_exit] - entry) / risk
        else:
            r_gross = (-1.0 if hit == 0 else float(mults[:hit].sum() / k))
        cost = costs.r_for(risk)
        trades.append(Trade(i, i_exit, d, entry, stop, tuple(targets), hit,
                            r_gross, r_gross - cost, i_exit - j, exit_reason))
        i = i_exit + 1                         # no overlapping positions

    eq = np.cumsum([t.r_net for t in trades]) if trades else np.array([])
    return Result(trades=trades, equity=eq)


def shift_causal(arr: np.ndarray, by: int = 1) -> np.ndarray:
    """Delay a feature so bar i can only see information complete at bar i-by."""
    out = np.full_like(np.asarray(arr, float), np.nan)
    if by <= 0:
        return np.asarray(arr, float)
    out[by:] = np.asarray(arr, float)[:-by]
    return out
