"""
CONFIDENCE AND PROBABILITY
==========================

The scorecard in strategy.py answers "how many of my rules agree?". That is a
useful number and it is not the number a trader actually wants, which is "how
often does this pay?". This module turns the first into an honest attempt at
the second, and is explicit about how much of it is measurement and how much is
modelling.

Probability
-----------
Start from barrier maths, not from optimism. With the stop 1R away and the
target kR away, a driftless market touches the target first 1/(1+k) of the
time. That is also, to the cent, the win rate you need to break even at kR — so
the null hypothesis for any strategy is "expectancy zero", and every point of
probability above it has to be earned.

Costs are subtracted first: paying c in R at entry means you are already c
closer to the stop and c further from the target. That single adjustment is
enough — at zero edge the resulting expectancy works out to exactly -c, which
is what a no-edge trade should cost you and nothing more.

Then the confluence score buys a log-odds adjustment, capped hard. A perfect
score is worth roughly twelve percentage points on a 1R target — not fifty.
Anything that quotes more than PROB_CEIL is refusing to print it.

Finally, if calibration.json exists (written by backtest.py --calibrate), the
modelled number is dragged toward what the strategy actually did on real bars,
weighted by how many trades that bucket saw. Eight trades barely move it; four
hundred override the model almost completely. That is the whole point: the
model is a placeholder until the data shows up.

Confidence
----------
A different question — not "will this pay?" but "how much should I trust my own
read?". It starts at the confluence score and loses points for hazards the
scorecard structurally cannot see: news hours, dead sessions, a bias EMA
degraded by short history, no room to the next swing. It is capped at 99
because 100 is a lie.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional, Sequence

import config as C

# --------------------------------------------------------------------------- #
#  Small maths helpers
# --------------------------------------------------------------------------- #
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _logit(p: float) -> float:
    p = _clamp(p, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _bar(pct: float, width: int = 14) -> str:
    filled = int(round(_clamp(pct, 0.0, 100.0) / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


# --------------------------------------------------------------------------- #
#  Score buckets — shared by the estimator and the backtester so the two can
#  never disagree about which bucket a trade belongs to.
# --------------------------------------------------------------------------- #
BUCKETS = ("<50", "50-59", "60-69", "70-79", "80-89", "90-100")


def bucket_of(score: float) -> str:
    s = float(score)
    if s >= 90:
        return "90-100"
    if s >= 80:
        return "80-89"
    if s >= 70:
        return "70-79"
    if s >= 60:
        return "60-69"
    if s >= 50:
        return "50-59"
    return "<50"


# --------------------------------------------------------------------------- #
#  Calibration file
# --------------------------------------------------------------------------- #
_cal_cache: Optional[tuple] = None


def _is_superseded(path: str) -> bool:
    """True when a local calibration is the old mode-only format.

    /backtest --calibrate used to write a file with one rate per mode and no
    TP2 and no per-strategy tables. It is read before the shipped one, so a
    single thin run from months ago silently outranks a measurement over
    4,939 trades — and because the old file has no TP2, that rung falls back
    to the model while TP1 and TP3 are calibrated, which is how a signal ends
    up quoting 41% for two-R and 40% for three-R.

    A local file in the current format still wins: a fresh measurement on the
    operator's own data should.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    if data.get("strategies"):
        return False
    modes = data.get("modes") or {}
    return not any(isinstance(m, dict) and "tp2" in m for m in modes.values())


def load_calibration(path: Optional[str] = None) -> dict:
    """Read calibration.json, re-reading only when the file's mtime changes.

    A long-running bot picks up a freshly written calibration without a
    restart. A missing or malformed file is not an error — it just means the
    probabilities stay modelled, and the signal says so.
    """
    global _cal_cache
    if path is None:
        path = C.CALIBRATION_FILE
        if not os.path.exists(path) or _is_superseded(path):
            # Nothing measured on this host, or what is here is the older and
            # thinner format — fall back to what shipped.
            path = getattr(C, "CALIBRATION_FALLBACK", path)

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}

    if _cal_cache is not None and _cal_cache[0] == (path, mtime):
        return _cal_cache[1]

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}

    if not isinstance(data, dict):
        return {}

    _cal_cache = ((path, mtime), data)
    return data


def calibration_status(cal: Optional[dict] = None) -> dict:
    """What the bot knows about its own reliability, for /calibration."""
    cal = load_calibration() if cal is None else cal
    modes = (cal or {}).get("modes") or {}
    return {
        "present": bool(modes),
        "generated": (cal or {}).get("generated"),
        "symbol": (cal or {}).get("symbol"),
        "source_note": (cal or {}).get("source"),
        "ladder": (cal or {}).get("ladder"),
        "modes": {
            name: {
                "trades": int(m.get("trades", 0)),
                "tp1": m.get("tp1"),
                "tp2": m.get("tp2"),
                "final": m.get("final"),
                "avg_r": m.get("avg_r"),
                "buckets": m.get("buckets") or {},
                "bars": m.get("bars"),
                "source": m.get("source"),
            }
            for name, m in modes.items()
            if isinstance(m, dict)
        },
        "strategies": {
            key: {
                mode: {
                    "trades": int(t.get("trades", 0)),
                    "tp1": t.get("tp1"), "tp2": t.get("tp2"),
                    "final": t.get("final"), "avg_r": t.get("avg_r"),
                    "buckets": t.get("buckets") or {},
                }
                for mode, t in (per_mode or {}).items() if isinstance(t, dict)
            }
            for key, per_mode in (((cal or {}).get("strategies") or {}).items())
        },
    }


# --------------------------------------------------------------------------- #
#  The model
# --------------------------------------------------------------------------- #
def null_probability(target_r: float, cost_r: Optional[float] = None) -> float:
    """Driftless odds of touching +target_r before -1R, after costs.

    This is the number to beat, not a forecast.
    """
    cost_r = C.COST_R if cost_r is None else cost_r
    return _clamp((1.0 - cost_r) / (1.0 + float(target_r)), 0.01, 0.99)


def breakeven_rate(target_r: float, cost_r: Optional[float] = None) -> float:
    """Win rate required for zero expectancy at target_r, after costs."""
    cost_r = C.COST_R if cost_r is None else cost_r
    return _clamp((1.0 + cost_r) / (1.0 + float(target_r)), 0.01, 0.99)


def model_probability(
    score: float, target_r: float, room_rr: Optional[float] = None,
    cost_r: Optional[float] = None,
) -> float:
    """Barrier odds shifted by the confluence score and by how much room the
    trade actually has before the next opposing swing."""
    cost_r = C.COST_R if cost_r is None else cost_r
    edge = C.PROB_EDGE_GAIN * (float(score) - C.PROB_EDGE_PIVOT) / 100.0

    if room_rr is not None and target_r > 0 and room_rr < target_r:
        shortfall = _clamp(1.0 - float(room_rr) / float(target_r), 0.0, 1.0)
        edge -= C.PROB_ROOM_PENALTY * shortfall

    p = _sigmoid(_logit(null_probability(target_r, cost_r)) + edge)
    return _clamp(p, C.PROB_FLOOR, C.PROB_CEIL)


def _shrink(observed: float, n: float, prior: float, weight: float) -> float:
    """Empirical Bayes. n real observations against `weight` pseudo-trades of
    prior. Small samples barely move the model; large ones bury it."""
    return (n * observed + weight * prior) / (n + weight)


def _table_for(cal: dict, mode: str, strategy: Optional[str]) -> tuple:
    """The most specific measured table available for this trade.

    A rate measured on a breakout says very little about a mean-reversion
    fade, so a strategy's own table is preferred whenever it exists. When it
    does not — a new ruleset, or one with too few trades to have been
    written — the pooled mode table is a weaker but still measured answer,
    and the model is the last resort.
    """
    if strategy:
        by_strat = ((cal or {}).get("strategies") or {}).get(strategy) or {}
        entry = by_strat.get(mode)
        if isinstance(entry, dict) and entry.get("trades"):
            return entry, True
    entry = ((cal or {}).get("modes") or {}).get(mode)
    return (entry if isinstance(entry, dict) else None), False


def _calibrate(
    p_model: float, mode: str, score: float, field: str, cal: dict,
    strategy: Optional[str] = None,
) -> tuple:
    """Drag the modelled probability toward measured results, in two stages:
    bucket rate -> mode-wide rate -> model. Returns (p, basis)."""
    entry, own = _table_for(cal, mode, strategy)
    if not isinstance(entry, dict):
        return p_model, {"source": "model", "n": 0, "bucket": None}

    w = C.CALIBRATION_PRIOR_WEIGHT
    floor, ceil = C.PROB_FLOOR, C.PROB_CEIL

    p = p_model
    n_mode = int(entry.get("trades", 0) or 0)
    r_mode = entry.get(field)
    mode_used = False
    if n_mode >= C.CALIBRATION_MIN_TRADES and isinstance(r_mode, (int, float)):
        p = _shrink(float(r_mode), n_mode, p_model, w)
        mode_used = True

    key = bucket_of(score)
    b = (entry.get("buckets") or {}).get(key) or {}
    n_b = int(b.get("n", 0) or 0)
    r_b = b.get(field)
    if n_b >= C.CALIBRATION_MIN_TRADES and isinstance(r_b, (int, float)):
        p = _shrink(float(r_b), n_b, p, w)
        return _clamp(p, floor, ceil), {
            "source": "strategy-calibrated" if own else "calibrated",
            "n": n_b, "bucket": key,
        }

    if mode_used:
        return _clamp(p, floor, ceil), {
            "source": "strategy-calibrated" if own else "mode-calibrated",
            "n": n_mode,
            "bucket": None,
        }

    return p_model, {"source": "model", "n": n_mode, "bucket": None}


# --------------------------------------------------------------------------- #
#  Public: probability of the setup paying
# --------------------------------------------------------------------------- #
def estimate(
    score: float,
    targets_r: Sequence[float],
    room_rr: Optional[float] = None,
    mode: str = "intraday",
    cal: Optional[dict] = None,
    cost_r: Optional[float] = None,
    strategy: Optional[str] = None,
) -> dict:
    """Probability that each target is reached before the stop, plus what that
    is worth in R.

    Targets are cumulative events: reaching 2R requires passing 1R, so the
    probabilities are forced to be non-increasing even if a thin calibration
    bucket says otherwise.
    """
    cal = load_calibration() if cal is None else cal
    # Dealing cost as a share of the stop, supplied per-instrument by
    # evaluate(). A tighter stop eats a bigger share of itself in spread,
    # so the odds get worse instead of the tightening looking free.
    cost_r = C.COST_R if cost_r is None else _clamp(float(cost_r), 0.0, 0.5)
    targets_r = [float(t) for t in (targets_r or [1.0])]

    # The calibration file counts three rungs. Naming them by position rather
    # than by index means a two-target ladder still calibrates both ends, and
    # the middle rung of a three-target ladder stops being skipped — it used
    # to fall through to the model while the rungs on either side of it were
    # measured, which made TP2 the least trustworthy number on the signal.
    last = len(targets_r) - 1
    FIELDS = {0: "tp1", 1: "tp2", 2: "final"}

    rows, prev = [], 1.0
    for i, k in enumerate(targets_r):
        field = "final" if i == last else FIELDS.get(i)
        p_model = model_probability(score, k, room_rr, cost_r)

        if field:
            p, basis = _calibrate(p_model, mode, score, field, cal, strategy)
        else:
            p, basis = p_model, {"source": "model", "n": 0, "bucket": None}

        p = min(p, prev)          # monotone: cannot pass 2R without passing 1R
        prev = p
        rows.append(
            {
                "r": k,
                "p": round(p, 4),
                "p_model": round(p_model, 4),
                "p_null": round(null_probability(k, cost_r), 4),
                "breakeven": round(breakeven_rate(k, cost_r), 4),
                "edge": round(p - breakeven_rate(k, cost_r), 4),
                "source": basis["source"],
                "sample": basis["n"],
                "bucket": basis["bucket"],
            }
        )

    p_first = rows[0]["p"]
    n = len(rows)

    # Expectancy assuming the plan is actually followed: equal slices, one per
    # target, stop to breakeven once the first target pays. If the first target
    # never trades, every slice loses the full 1R.
    scaled = sum(r["p"] * r["r"] for r in rows) / n
    expectancy_r = scaled - (1.0 - p_first)

    # The blunter alternative: everything rides to the last target.
    last = rows[-1]
    expectancy_all_out = last["p"] * last["r"] - (1.0 - last["p"])

    sources = {r["source"] for r in rows}
    source = (
        "calibrated" if "calibrated" in sources
        else "mode-calibrated" if "mode-calibrated" in sources
        else "model"
    )

    return {
        "targets": rows,
        "p_first": p_first,
        "p_final": last["p"],
        "expectancy_r": round(expectancy_r, 3),
        "expectancy_all_out": round(expectancy_all_out, 3),
        "plan": (
            "all out at TP1" if n == 1
            else "half at TP1, rest to TP2" if n == 2
            else f"equal slices across {n} targets"
        ),
        "expectancy_note": (
            f"equal slices to {n} target{'s' if n > 1 else ''}"
            + (", stop to breakeven after the first" if n > 1 else "")
        ),
        "breakeven_first": rows[0]["breakeven"],
        "source": source,
        "sample": max(r["sample"] for r in rows),
        "cost_r": round(cost_r, 4),
    }


# --------------------------------------------------------------------------- #
#  Public: confidence in the read
# --------------------------------------------------------------------------- #
PENALTY_TEXT = {
    "outside_session": "outside the session window",
    "news_hour": "high-impact news hour",
    "short_history": "bias EMA degraded (short history)",
    "no_room": "no room to the next opposing swing",
    "half_trigger": "only one momentum trigger fired",
    "weak_structure": "swing structure not fully intact",
    "odd_volatility": "volatility outside the normal band",
    "ageing_data": "last candle is ageing",
    "weak_candle": "confirming candle is weak",
}


def confidence(score: float, flags: Optional[dict] = None,
               clean_sweep: bool = False) -> dict:
    """Confluence score minus the hazards the scorecard cannot see.

    Returns the number plus every adjustment that produced it, so the signal
    can show its working instead of asserting a figure.
    """
    flags = flags or {}
    value = float(score)
    adjustments = []

    for key, penalty in C.CONFIDENCE_PENALTIES.items():
        if flags.get(key):
            value -= penalty
            adjustments.append(
                {"key": key, "points": -int(penalty), "text": PENALTY_TEXT.get(key, key)}
            )

    if clean_sweep:
        bonus = C.CONFIDENCE_CLEAN_SWEEP_BONUS
        value += bonus
        adjustments.append(
            {"key": "clean_sweep", "points": int(bonus),
             "text": "every scorecard line passed"}
        )

    adjustments.sort(key=lambda a: a["points"])

    return {
        "value": int(round(_clamp(value, 5.0, 99.0))),
        "from_score": int(round(float(score))),
        "adjustments": adjustments,
        "drag": int(round(float(score) - _clamp(value, 5.0, 99.0))),
    }


# --------------------------------------------------------------------------- #
#  Rendering — plain text, shared by both front ends so they cannot drift
# --------------------------------------------------------------------------- #
SOURCE_NOTE = {
    "calibrated": "calibrated on {n} backtested trades in the {bucket} bucket",
    "mode-calibrated": "calibrated on {n} backtested trades (all buckets)",
    "strategy-calibrated": "calibrated on {n} of this strategy's own trades",
    "model": "model estimate — no backtest calibration yet",
}


def realised_r(targets_hit: int, tp_multiples, cost_r: float = None) -> float:
    """What a finished trade actually paid, in R.

    ONE definition, used by the backtester, the live journal and the
    expectancy figure printed on every signal. They each had their own before
    this, and they disagreed: the backtester scored a trade that touched TP1
    and TP2 before reversing as a full -1R loss, the journal scored the same
    trade +0.33R, and the signal quoted an expectancy derived from neither.

    The plan being modelled is the one the signal states: equal slices to each
    target, and once the first fills the remainder rides at breakeven, so the
    worst case after TP1 is keeping TP1's slice and scratching the rest.

        3 targets at 1R/2R/3R, none hit   -> -1R minus costs
        TP1 only                          -> 1/3 = +0.33R
        TP1 and TP2                       -> (1+2)/3 = +1.00R
        all three                         -> (1+2+3)/3 = +2.00R
    """
    cost_r = C.COST_R if cost_r is None else cost_r
    mults = list(tp_multiples) or [1.0]
    if targets_hit <= 0:
        return -1.0 - cost_r
    return sum(mults[:targets_hit]) / len(mults) - cost_r


def read_block(res: dict, width: int = 14) -> str:
    """The confidence/probability table both front ends print inside a <pre>.

    Expects an evaluate() result. Returns "" if there is nothing to show
    (a hard veto never gets this far).
    """
    pr = res.get("probability")
    conf = res.get("confidence") or {}
    score = int(res.get("score") or 0)

    lines = [f"{'Confluence':<12}{score:>4}%  {_bar(score, width)}"]
    if conf:
        c = int(conf.get("value", 0))
        lines.append(f"{'Confidence':<12}{c:>4}%  {_bar(c, width)}")

    if not pr:
        return "\n".join(lines)

    for i, t in enumerate(pr["targets"], start=1):
        pct = t["p"] * 100.0
        label = f"P(TP{i} {t['r']:g}R)"
        lines.append(f"{label:<12}{pct:>4.0f}%  {_bar(pct, width)}")

    lines.append("")
    lines.append(
        f"{'Expectancy':<12}{pr['expectancy_r']:>+5.2f}R  {pr['plan']}"
    )
    lines.append(
        f"{'Breakeven':<12}{pr['breakeven_first'] * 100:>5.0f}%  win rate needed "
        f"at {pr['targets'][0]['r']:g}R"
    )
    return "\n".join(lines)


def basis_note(res: dict) -> str:
    """One line saying how much of the probability is measured and how much is
    guessed. Never omit this — it is the difference between a tool and a toy."""
    pr = res.get("probability")
    if not pr:
        return ""
    row = pr["targets"][0]
    note = SOURCE_NOTE[pr["source"]].format(
        n=pr["sample"], bucket=row.get("bucket") or "matching"
    )
    return (
        f"{note}. Costs assumed {pr['cost_r']:.2f}R; expectancy assumes "
        f"{pr['expectancy_note']}."
    )


def drag_note(res: dict, limit: int = 3) -> str:
    """Why confidence sits below the confluence score."""
    conf = res.get("confidence") or {}
    drags = [a for a in conf.get("adjustments", []) if a["points"] < 0][:limit]
    if not drags:
        return ""
    return "Confidence drags: " + " · ".join(
        f"{a['text']} \u2212{abs(a['points'])}" for a in drags
    )


def calibration_report(lang: str = "en") -> str:
    """Answer one question in plain language: when the bot says 60%, is it?

    Written as Telegram HTML rather than a monospace table. A beginner
    reading "bucket / n / TP1 / final / pred" learns nothing; the useful
    content is whether the odds on their signals were measured against real
    trades or are still the model's opinion.
    """
    import i18n                                   # noqa: PLC0415
    st = calibration_status()
    t = lambda k, **kw: i18n.t(k, lang, **kw)     # noqa: E731

    head = f"{t('cal_title')}\n━━━━━━━━━━━━━━━━━━━━\n\n"

    if not st["present"]:
        return (head + t("cal_none") + "\n\n" + t("cal_none_fix"))

    out = head + t("cal_intro") + "\n\n"

    def pc(v):
        return f"{v:.0%}" if isinstance(v, (int, float)) else "—"

    out += f"<b>{t('cal_overall')}</b>\n"
    for name in ("scalp", "intraday", "swing"):
        m = st["modes"].get(name)
        if not m:
            continue
        out += (f"  {name}: <b>{pc(m['tp1'])}</b> reach TP1 · "
                f"{pc(m['tp2'])} TP2 · {pc(m['final'])} TP3"
                f"  <i>({m['trades']} trades)</i>\n")

    strats = st.get("strategies") or {}
    if strats:
        from strategies import REGISTRY               # noqa: PLC0415
        out += f"\n<b>{t('cal_per_strategy')}</b>\n"
        for key in sorted(strats):
            s = REGISTRY.get(key)
            label = f"{s.icon} {s.name}" if s else key
            rows = strats[key]
            parts = [f"{mo[:3]} {pc(v['tp1'])}" for mo, v in sorted(rows.items())]
            out += f"  {label}: " + " · ".join(parts) + "\n"

    out += "\n" + t("cal_shrink", n=int(C.CALIBRATION_PRIOR_WEIGHT))
    if st.get("generated"):
        out += f"\n<i>{t('cal_measured_on', when=str(st['generated'])[:10])}</i>"
    return out
