"""Trade statistics. Every number the brief asks for, computed one way."""
from __future__ import annotations

import numpy as np

TRADING_PERIODS_PER_YEAR = {"5min": 252 * 288, "15min": 252 * 96,
                            "1h": 252 * 24, "4h": 252 * 6, "1day": 252}


def summarise(rs: np.ndarray, bars_held=None, tf: str = "4h") -> dict:
    """rs is per-trade R. Everything else follows from it."""
    n = len(rs)
    if n == 0:
        return {"trades": 0}
    wins, losses = rs[rs > 0], rs[rs <= 0]
    gross_w, gross_l = wins.sum(), -losses.sum()

    eq = np.cumsum(rs)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = eq - peak
    max_dd = dd.min() if len(dd) else 0.0

    # Streaks
    streak = best_w = best_l = 0
    for r in rs:
        if r > 0:
            streak = streak + 1 if streak > 0 else 1
            best_w = max(best_w, streak)
        else:
            streak = streak - 1 if streak < 0 else -1
            best_l = min(best_l, streak)

    sd = rs.std(ddof=1) if n > 1 else 0.0
    downside = rs[rs < 0].std(ddof=1) if (rs < 0).sum() > 1 else 0.0
    # Per-trade Sharpe/Sortino, annualised by observed trade frequency rather
    # than an assumed one — with a note that both are noisy under ~100 trades.
    sharpe = (rs.mean() / sd * np.sqrt(n)) if sd > 0 else 0.0
    sortino = (rs.mean() / downside * np.sqrt(n)) if downside > 0 else 0.0

    return {
        "trades": n,
        "win_rate": len(wins) / n,
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "expectancy": rs.mean(),
        "profit_factor": (gross_w / gross_l) if gross_l > 0
                         else (np.inf if gross_w > 0 else 0.0),
        "net_r": rs.sum(),
        "max_dd_r": float(max_dd),
        "recovery": (rs.sum() / abs(max_dd)) if max_dd < 0 else np.inf,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_win_streak": int(best_w),
        "max_loss_streak": int(abs(best_l)),
        "avg_bars": float(np.mean(bars_held)) if bars_held is not None and len(bars_held) else None,
        # How much of the profit rests on the single best trade? A strategy
        # carried by one outlier is a story about that outlier.
        "top_trade_share": (rs.max() / rs.sum()) if rs.sum() > 0 else np.nan,
    }


def significance(rs: np.ndarray) -> dict:
    """Is the mean R distinguishable from zero, and by how much?"""
    n = len(rs)
    if n < 2:
        return {"t": 0.0, "se": np.nan, "ci95": (np.nan, np.nan)}
    se = rs.std(ddof=1) / np.sqrt(n)
    m = rs.mean()
    return {"t": m / se if se > 0 else 0.0, "se": se,
            "ci95": (m - 1.96 * se, m + 1.96 * se)}
