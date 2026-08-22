"""
THE THREE SELECTABLE STRATEGIES
===============================

Ronin Edge is the original and is untouched — it still runs through
strategy.evaluate() exactly as it always has. The two additions are not
re-tunings of it; they look for different things and will disagree with it
and with each other, which is the point of offering a choice.

  Ronin Edge     trend pullback. Wait for an established trend, buy the
                 retracement into the EMA20 zone when momentum turns back.
                 Trades with the trend, entering against the short-term move.

  Crimson Flow   momentum breakout. Wait for a Donchian extreme to give way
                 in the direction the higher timeframe already permits, with
                 ADX rising and range expanding. Trades with the move,
                 entering as it accelerates.

  Kage Protocol  volatility squeeze. Wait for Bollinger width to compress to
                 a multi-week low, then take the first decisive close out of
                 the range. Trades the transition from quiet to loud, and is
                 flat whenever there is no squeeze to resolve.

All three return the identical result contract, so the view, the journal,
the probability model and the lifecycle tracker neither know nor care which
one produced a signal.

ON THE EVIDENCE
---------------
Ten candidate rulesets live in `candidates.py` and `bakeoff.py` ranks them
on real history. Crimson Flow and Kage Protocol are the two the design
argument favours — each covers a market state Ronin structurally cannot
trade, so the three together span trend continuation, breakout and
transition rather than crowding one regime.

That is a design argument, not a measurement. It has NOT been confirmed on
market data, because the environment this was written in has no market data
access. Run `python bakeoff.py --live` against your own key before treating
either as validated; it prints the full table and will happily tell you that
a different pair of candidates is better.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

import config as C
import indicators as ind
import instruments as I
import probability as prob
import strategy as base
from strategy import FLAT, LONG, SHORT


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    blurb_en: str
    blurb_id: str
    evaluate: Callable


# --------------------------------------------------------------------------- #
#  Shared scaffolding
# --------------------------------------------------------------------------- #
def _common_vetoes(e: dict, t: dict, b: dict, spec, now_utc, entry_df) -> list:
    """The gates every ruleset honours: dead tape, news spikes, stale data."""
    out = []
    if e["atr_ratio"] and e["atr_ratio"] > C.VOL_SPIKE_MULT:
        out.append(f"ATR {e['atr_ratio']:.1f}x normal — news spike, stand aside")
    if e["atr_ratio"] and e["atr_ratio"] < C.VOL_DEAD_MULT:
        out.append(f"ATR {e['atr_ratio']:.1f}x normal — dead tape")
    tf_seconds = base.TF_SECONDS.get(spec.entry_tf, 900)
    age = (now_utc - entry_df.index[-1].to_pydatetime()).total_seconds()
    if age > tf_seconds * base.STALE_BARS:
        out.append(f"Data is stale ({age / 60:.0f} min old) — market likely closed")
    return out


def _finalise(out: dict, *, direction, reasons, missing, trigger_present,
              e, entry_df, highs, lows, atr_e, spec, inst, balance, risk_pct,
              risk_usd, now_utc, in_session, short_history) -> dict:
    """Everything downstream of 'which way and how strongly'.

    Levels, order type, confidence and probability are strategy-independent:
    once a ruleset has produced a direction and a scorecard, the arithmetic
    that turns those into a risked, priced trade is the same for all of them.
    """
    score = sum(r["points"] for r in reasons)
    out["score"] = int(round(score))
    out["reasons"] = reasons
    out["missing"] = missing
    out["direction"] = direction
    out["atr_ratio"] = round(float(e["atr_ratio"]), 3) if e.get("atr_ratio") else None

    def build(px):
        return base._build_levels(px, direction=direction, highs=highs, lows=lows,
                                  atr_e=atr_e, spec=spec, inst=inst,
                                  balance=balance, risk_pct=risk_pct,
                                  risk_usd=risk_usd)

    out["levels"], room_rr, _ = build(float(e["close"]))

    if out["score"] >= C.ENTRY_MIN_SCORE and trigger_present:
        if room_rr is not None and room_rr < spec.min_rr:
            out["decision"] = "WAIT"
            missing.insert(0, f"only {room_rr:.1f}R of room to the next swing "
                              f"(need {spec.min_rr}R)")
        else:
            out["decision"] = "ENTRY"
    elif out["score"] >= C.WAIT_MIN_SCORE:
        out["decision"] = "WAIT"
    else:
        out["decision"] = "NO TRADE"

    out["order"] = base._order_plan(out["decision"], direction, float(e["close"]),
                                    e, entry_df, spec, atr_e, inst)
    if out["order"]["kind"] != "market":
        out["levels"], room_rr, _ = build(out["order"]["price"])

    news_hour = now_utc.hour in C.NEWS_WARNING_HOURS_UTC
    out["news_warning"] = news_hour

    flags = {
        "outside_session": not in_session,
        "news_hour": news_hour,
        "short_history": short_history,
        "no_room": room_rr is not None and room_rr < spec.min_rr,
        "half_trigger": not trigger_present,
        "weak_structure": False,
        "odd_volatility": not (0.7 <= (e["atr_ratio"] or 1.0) <= 1.8),
        "ageing_data": False,
        "weak_candle": False,
    }
    out["confidence"] = prob.confidence(out["score"], flags,
                                        clean_sweep=all(r["ok"] for r in reasons))
    out["probability"] = prob.estimate(
        score=out["score"], targets_r=spec.tp_multiples, room_rr=room_rr,
        mode=spec.name, cost_r=inst.cost_r(out["levels"]["risk_points"]),
    )
    return out


def _scaffold(entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
              instrument, risk_usd, strategy_key):
    """Snapshot the three timeframes and set up the empty result."""
    now_utc = now_utc or datetime.now(timezone.utc)
    inst = instrument or I.GOLD
    balance = C.ACCOUNT_BALANCE if balance is None else balance
    risk_pct = C.RISK_PCT if risk_pct is None else risk_pct

    e = base.snapshot(entry_df)
    t = base.snapshot(trend_df)
    b = base.snapshot(bias_df)

    out = {
        "mode": spec.name,
        "strategy": strategy_key,
        "instrument": inst.key,
        "symbol": inst.symbol,
        "timeframes": {"entry": spec.entry_tf, "trend": spec.trend_tf,
                       "bias": spec.bias_tf},
        "decision": "NO TRADE",
        "direction": FLAT,
        "score": 0,
        "reasons": [],
        "vetoes": [],
        "missing": [],
        "levels": None,
        "price": round(float(e["close"]), inst.digits),
        "as_of": entry_df.index[-1].to_pydatetime(),
    }
    return out, e, t, b, inst, balance, risk_pct, now_utc


# --------------------------------------------------------------------------- #
#  Crimson Flow — momentum breakout continuation
# --------------------------------------------------------------------------- #
def evaluate_crimson(entry_df, trend_df, bias_df, spec, now_utc=None,
                     balance=None, risk_pct=None, instrument=None,
                     risk_usd=None) -> dict:
    """Take the break of a Donchian extreme in the higher timeframe's
    direction, when the range is expanding and ADX is rising.

    The opposite entry to Ronin: this buys strength rather than weakness, so
    the two disagree most exactly where it matters — a trend that is running
    away is Crimson's setup and Ronin's "no pullback yet".
    """
    out, e, t, b, inst, balance, risk_pct, now_utc = _scaffold(
        entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
        instrument, risk_usd, "crimson")

    vetoes = _common_vetoes(e, t, b, spec, now_utc, entry_df)
    bias_dir, _ = base._bias_direction(b)
    if bias_dir == FLAT:
        vetoes.append("Bias timeframe has no direction — nothing to continue")
    if t["adx"] < C.ADX_GATE:
        vetoes.append(f"{spec.trend_tf} ADX {t['adx']:.1f} — no trend to break out of")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    dc = ind.donchian(entry_df, C.CRIMSON_CHANNEL)
    upper = float(dc["upper"].iloc[-1])
    lower = float(dc["lower"].iloc[-1])
    close = float(e["close"])
    bar = entry_df.iloc[-1]
    rng = float(bar["high"] - bar["low"]) or 1e-9

    broke_up = close > upper
    broke_dn = close < lower
    direction = LONG if broke_up else SHORT if broke_dn else bias_dir
    if bias_dir != FLAT and direction != bias_dir:
        # A break against the higher timeframe is a reversal, not a
        # continuation. This ruleset does not trade those.
        direction = bias_dir
        broke_up = broke_dn = False

    reasons, missing = [], []

    def add(key, ok, points, mx, text):
        reasons.append({"key": key, "ok": bool(ok),
                        "points": float(points), "max": mx, "text": text})
        if not ok:
            missing.append(text)

    add("bias_align", True, C.CRIMSON_WEIGHTS["bias_align"],
        C.CRIMSON_WEIGHTS["bias_align"],
        f"{spec.bias_tf} bias {base.DIR_NAME[bias_dir].lower()}")

    broke = broke_up or broke_dn
    add("breakout", broke, C.CRIMSON_WEIGHTS["breakout"] if broke else 0,
        C.CRIMSON_WEIGHTS["breakout"],
        f"{C.CRIMSON_CHANNEL}-bar channel broken"
        if broke else f"inside the {C.CRIMSON_CHANNEL}-bar channel — no break yet")

    adx_now, adx_prev = t["adx"], t["adx_prev"]
    rising = adx_now > adx_prev
    adx_pts = C.CRIMSON_WEIGHTS["adx_rising"] * (1.0 if rising else 0.3)
    add("adx_rising", rising, adx_pts, C.CRIMSON_WEIGHTS["adx_rising"],
        f"{spec.trend_tf} ADX {adx_now:.1f} "
        + ("rising" if rising else "flat or falling"))

    # A breakout bar that closes mid-range is a failed break more often than
    # a successful one, so where in its own range it closed is scored.
    pos = (float(bar["close"] - bar["low"]) / rng if direction == LONG
           else float(bar["high"] - bar["close"]) / rng)
    strong_close = pos >= C.CRIMSON_CLOSE_POS
    add("close_position", strong_close,
        C.CRIMSON_WEIGHTS["close_position"] * min(1.0, pos / C.CRIMSON_CLOSE_POS),
        C.CRIMSON_WEIGHTS["close_position"],
        f"closed {pos:.0%} into its range"
        + ("" if strong_close else " — weak for a breakout"))

    expanding = (e["atr_ratio"] or 1.0) >= C.CRIMSON_ATR_EXPANSION
    add("atr_expansion", expanding,
        C.CRIMSON_WEIGHTS["atr_expansion"] if expanding else 0,
        C.CRIMSON_WEIGHTS["atr_expansion"],
        f"range {e['atr_ratio']:.2f}x normal"
        + ("" if expanding else " — not expanding"))

    in_sess = base._in_session(now_utc, spec)
    add("session", in_sess, C.CRIMSON_WEIGHTS["session"] if in_sess else 0,
        C.CRIMSON_WEIGHTS["session"],
        "inside session" if in_sess else "outside session window")

    highs, lows = ind.last_swings(entry_df)
    return _finalise(out, direction=direction, reasons=reasons, missing=missing,
                     trigger_present=broke and strong_close, e=e,
                     entry_df=entry_df, highs=highs, lows=lows,
                     atr_e=float(e["atr"]), spec=spec, inst=inst,
                     balance=balance, risk_pct=risk_pct, risk_usd=risk_usd,
                     now_utc=now_utc, in_session=in_sess,
                     short_history=b["ema_slow_n"] < 200)


# --------------------------------------------------------------------------- #
#  Kage Protocol — volatility squeeze expansion
# --------------------------------------------------------------------------- #
def evaluate_kage(entry_df, trend_df, bias_df, spec, now_utc=None,
                  balance=None, risk_pct=None, instrument=None,
                  risk_usd=None) -> dict:
    """Trade the moment a quiet market stops being quiet.

    Bollinger width has to be near the bottom of its own recent range — an
    actual squeeze, measured as a percentile of the last N readings — and the
    signal is the first close outside the band. With no squeeze there is no
    setup, and this ruleset says so rather than finding something to trade.
    """
    out, e, t, b, inst, balance, risk_pct, now_utc = _scaffold(
        entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
        instrument, risk_usd, "kage")

    vetoes = _common_vetoes(e, t, b, spec, now_utc, entry_df)

    bb = ind.bollinger(entry_df["close"], C.KAGE_BB_PERIOD, C.KAGE_BB_K)
    width = bb["width"]
    hist = width.tail(C.KAGE_SQUEEZE_LOOKBACK).dropna()
    if len(hist) < C.KAGE_SQUEEZE_LOOKBACK // 2 or pd.isna(width.iloc[-1]):
        out["vetoes"] = vetoes + ["Not enough history to measure a squeeze"]
        return out

    now_w = float(width.iloc[-1])
    pctile = float((hist <= now_w).mean())      # 0 = tightest in the window
    squeezed = pctile <= C.KAGE_SQUEEZE_PCTILE
    if not squeezed:
        vetoes.append(f"No squeeze — band width is at the {pctile:.0%} mark, "
                      f"needs {C.KAGE_SQUEEZE_PCTILE:.0%} or tighter")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    close = float(e["close"])
    up, lo = float(bb["upper"].iloc[-1]), float(bb["lower"].iloc[-1])
    broke_up, broke_dn = close > up, close < lo
    bias_dir, _ = base._bias_direction(b)
    direction = LONG if broke_up else SHORT if broke_dn else (bias_dir or LONG)

    reasons, missing = [], []

    def add(key, ok, points, mx, text):
        reasons.append({"key": key, "ok": bool(ok), "points": float(points),
                        "max": mx, "text": text})
        if not ok:
            missing.append(text)

    add("squeeze", True,
        C.KAGE_WEIGHTS["squeeze"] * (1.0 - pctile / max(C.KAGE_SQUEEZE_PCTILE, 1e-9)
                                     * 0.4),
        C.KAGE_WEIGHTS["squeeze"],
        f"band width at the {pctile:.0%} mark — squeezed")

    broke = broke_up or broke_dn
    add("expansion", broke, C.KAGE_WEIGHTS["expansion"] if broke else 0,
        C.KAGE_WEIGHTS["expansion"],
        "closed outside the band" if broke else "still inside the band — no break yet")

    agrees = bias_dir == FLAT or direction == bias_dir
    add("bias_agree", agrees, C.KAGE_WEIGHTS["bias_agree"] if agrees else 0,
        C.KAGE_WEIGHTS["bias_agree"],
        "break agrees with the higher timeframe" if agrees
        else "break fights the higher timeframe")

    rsi_now = float(e["rsi"].iloc[-1])
    not_extreme = C.RSI_OVERSOLD < rsi_now < C.RSI_OVERBOUGHT
    add("rsi_room", not_extreme, C.KAGE_WEIGHTS["rsi_room"] if not_extreme else 0,
        C.KAGE_WEIGHTS["rsi_room"],
        f"RSI {rsi_now:.0f} has room" if not_extreme
        else f"RSI {rsi_now:.0f} already extreme")

    in_sess = base._in_session(now_utc, spec)
    add("session", in_sess, C.KAGE_WEIGHTS["session"] if in_sess else 0,
        C.KAGE_WEIGHTS["session"],
        "inside session" if in_sess else "outside session window")

    highs, lows = ind.last_swings(entry_df)
    return _finalise(out, direction=direction, reasons=reasons, missing=missing,
                     trigger_present=broke, e=e, entry_df=entry_df,
                     highs=highs, lows=lows, atr_e=float(e["atr"]), spec=spec,
                     inst=inst, balance=balance, risk_pct=risk_pct,
                     risk_usd=risk_usd, now_utc=now_utc, in_session=in_sess,
                     short_history=b["ema_slow_n"] < 200)


def _evaluate_ronin(*a, **kw) -> dict:
    res = base.evaluate(*a, **kw)
    res.setdefault("strategy", "ronin")
    return res


REGISTRY: dict[str, Strategy] = {
    "ronin": Strategy(
        "ronin", "Ronin Edge",
        "Trend pullback. Waits for an established trend, then buys the "
        "retracement into the EMA20 zone when momentum turns back.",
        "Pullback tren. Menunggu tren terbentuk, lalu masuk saat harga "
        "retrace ke zona EMA20 dan momentum berbalik.",
        _evaluate_ronin),
    "crimson": Strategy(
        "crimson", "Crimson Flow",
        "Momentum breakout. Takes the break of a 20-bar channel in the "
        "higher timeframe's direction, with ADX rising and range expanding.",
        "Breakout momentum. Masuk saat channel 20-bar jebol searah timeframe "
        "besar, dengan ADX naik dan range melebar.",
        evaluate_crimson),
    "kage": Strategy(
        "kage", "Kage Protocol",
        "Volatility squeeze. Waits for Bollinger width to compress to a "
        "multi-week low, then takes the first close out of the range.",
        "Squeeze volatilitas. Menunggu lebar Bollinger menyempit ke level "
        "terendah, lalu masuk pada close pertama di luar range.",
        evaluate_kage),
}

ORDER = ("ronin", "crimson", "kage")
DEFAULT = "ronin"


def get(key: Optional[str]) -> Strategy:
    return REGISTRY.get((key or "").lower(), REGISTRY[DEFAULT])


def evaluate(key: Optional[str], *a, **kw) -> dict:
    return get(key).evaluate(*a, **kw)


# --------------------------------------------------------------------------- #
#  Zanshin Sweep — liquidity grab at a level, sized by ATR
# --------------------------------------------------------------------------- #
def evaluate_zanshin(entry_df, trend_df, bias_df, spec, now_utc=None,
                     balance=None, risk_pct=None, instrument=None,
                     risk_usd=None) -> dict:
    """Buy where the stops just got taken.

    The pattern is the oldest one in the book: price trades through a level
    people are defending, triggers the orders resting beyond it, and closes
    back on the original side. Whoever sold the low has already been filled;
    the supply is gone. That is what "liquidity" means here, and it is the
    only sense of it available — no venue publishes an order book for spot
    gold, and forex volume from a retail feed is a fiction. Rather than
    dress a volume proxy up as depth, this reads liquidity where it is
    actually observable: as the stop pools behind levels the market has
    turned at more than once, and as the sessions when those pools are deep
    enough to be worth running.

      SUPPORT/RESISTANCE  pivots clustered within 0.45 ATR; a level needs two
                          touches to exist at all
      LIQUIDITY           the sweep — pierce the level, close back through it
      VOLATILITY          two gates: the tape must be in a sane ATR band, and
                          the sweep bar itself must expand, because a grab
                          that happens in a dead range is not a grab
      ATR                 sets the sweep depth threshold, the level tolerance,
                          and the stop, so every distance scales with the
                          instrument instead of being a number in points

    The stop sits beyond the sweep's own extreme. That is the honest
    invalidation: if price returns through the wick, the grab failed and the
    reason for the trade is gone.

    Nothing here was tuned against a backtest. The thresholds are round
    numbers picked from the shape of the pattern, so that the first
    measurement is a test rather than a memory.
    """
    out, e, t, b, inst, balance, risk_pct, now_utc = _scaffold(
        entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
        instrument, risk_usd, "zanshin")
    vetoes = _common_vetoes(e, t, b, spec, now_utc, entry_df)

    atr_e = float(e["atr"])
    ratio = e["atr_ratio"] or 1.0
    if not (C.ZANSHIN_VOL_MIN <= ratio <= C.ZANSHIN_VOL_MAX):
        vetoes.append(f"ATR {ratio:.2f}x normal — outside the band a sweep "
                      f"can be read in")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    levels = ind.sr_levels(entry_df, atr_e, tol_atr=C.ZANSHIN_LEVEL_TOL_ATR,
                           lookback=C.ZANSHIN_LEVEL_LOOKBACK,
                           min_touches=C.ZANSHIN_MIN_TOUCHES)
    if not levels:
        out["vetoes"] = ["No level with two touches — nothing to sweep"]
        return out

    bar = entry_df.iloc[-1]
    close = float(e["close"])
    bias_dir, _ = base._bias_direction(b)

    # Try both directions against every nearby level; take the best sweep.
    best = None
    for lv in levels:
        if abs(close - lv["price"]) > C.ZANSHIN_MAX_DISTANCE_ATR * atr_e:
            continue
        for direction in (LONG, SHORT):
            s = ind.sweep(bar, lv["price"], direction, atr_e,
                          min_depth_atr=C.ZANSHIN_MIN_DEPTH_ATR)
            if not s:
                continue
            score = s["depth_atr"] + s["close_pos"] + 0.2 * lv["touches"]
            if best is None or score > best["rank"]:
                best = {"rank": score, "dir": direction, "level": lv, "sweep": s}

    if best is None:
        out["vetoes"] = ["No liquidity sweep on this bar"]
        return out

    direction = best["dir"]
    lv, sw = best["level"], best["sweep"]
    reasons, missing = [], []

    def add(key, ok, pts, mx, text):
        reasons.append({"key": key, "ok": bool(ok), "points": float(pts),
                        "max": mx, "text": text})
        if not ok:
            missing.append(text)

    W = C.ZANSHIN_WEIGHTS

    touches = min(lv["touches"], 4)
    add("level", True, W["level"] * (0.55 + 0.15 * (touches - 2)), W["level"],
        f"level at {lv['price']:.5g} held {lv['touches']}x")

    deep = sw["depth_atr"] >= C.ZANSHIN_GOOD_DEPTH_ATR
    add("sweep", True, W["sweep"] * (1.0 if deep else 0.6), W["sweep"],
        f"swept {sw['depth_atr']:.2f} ATR through it"
        + ("" if deep else " — shallow grab"))

    strong = sw["close_pos"] >= C.ZANSHIN_MIN_CLOSE_POS
    add("reclaim", strong, W["reclaim"] * min(1.0, sw["close_pos"] / C.ZANSHIN_MIN_CLOSE_POS),
        W["reclaim"],
        f"closed {sw['close_pos']:.0%} back through the level"
        + ("" if strong else " — weak reclaim"))

    expanded = sw["range_atr"] >= C.ZANSHIN_MIN_RANGE_ATR
    add("expansion", expanded, W["expansion"] if expanded else 0, W["expansion"],
        f"sweep bar spanned {sw['range_atr']:.2f} ATR"
        + ("" if expanded else " — too quiet to be a real grab"))

    # Liquidity, in the only sense a retail feed can support: the sessions
    # when the pools behind a level are actually worth running.
    in_sess = base._in_session(now_utc, spec)
    add("liquidity", in_sess, W["liquidity"] if in_sess else 0, W["liquidity"],
        "inside the liquid session" if in_sess
        else "thin session — sweeps here are often just spread")

    against = bias_dir != FLAT and direction != bias_dir and t["adx"] >= C.ADX_GOOD
    add("context", not against, 0 if against else W["context"], W["context"],
        "higher timeframe not fighting it" if not against
        else f"{spec.trend_tf} trending hard the other way")

    highs, lows = ind.last_swings(entry_df)
    # The sweep's own extreme is the invalidation, so it becomes the swing the
    # stop hides behind.
    if direction == LONG:
        lows = [(len(entry_df) - 1, sw["extreme"])] + list(lows)
    else:
        highs = [(len(entry_df) - 1, sw["extreme"])] + list(highs)

    return _finalise(out, direction=direction, reasons=reasons, missing=missing,
                     trigger_present=strong and expanded and not against,
                     e=e, entry_df=entry_df, highs=highs, lows=lows,
                     atr_e=atr_e, spec=spec, inst=inst, balance=balance,
                     risk_pct=risk_pct, risk_usd=risk_usd, now_utc=now_utc,
                     in_session=in_sess, short_history=b["ema_slow_n"] < 200)


REGISTRY["zanshin"] = Strategy(
    "zanshin", "Zanshin Sweep",
    "Liquidity sweep. Waits for price to run the stops beyond a level that "
    "has held twice, then close back through it, and takes the reclaim with "
    "the stop behind the wick.",
    "Sweep likuiditas. Menunggu harga menyapu stop di balik level yang sudah "
    "teruji dua kali, lalu close kembali menembusnya, dan masuk dengan stop "
    "di balik ekor candle.",
    evaluate_zanshin)
ORDER = ("ronin", "crimson", "kage", "zanshin")
