"""
TEN CANDIDATE RULESETS
======================

The pool §14 asks for. Each is rule-based, objective, and testable: given
the same candles it makes the same decision, and nothing in here consults a
number that was not available at the time of the bar.

They are deliberately spread across market states rather than being ten
tunings of one idea. Three of them are the live strategies (pullback,
breakout, squeeze); the other seven attack regimes those three ignore.

  1  ronin       trend pullback (live: Ronin Edge)
  2  crimson     channel breakout (live: Crimson Flow)
  3  kage        volatility squeeze (live: Kage Protocol)
  4  macd_cross  MACD crossing its zero line with the trend behind it
  5  rsi_fade    fade an RSI extreme, but only when ADX says there is no trend
  6  ribbon      stacked-EMA continuation on a shallow dip
  7  orb         opening-range breakout of the session's first hour
  8  inside_bar  compression: an inside bar inside a trend, taken on the break
  9  pivot_bounce  reaction off the prior session's pivot levels
 10  adx_expand  ADX crossing up through its threshold as a trend is born

BIAS CONTROL
------------
Every candidate reads only closed bars. Donchian channels are shifted so the
current bar cannot break a channel it helped build. The backtester fills at
the bar after the signal and scores a bar touching both target and stop as a
loss. None of that makes a backtest true, but it removes the three ways one
most commonly becomes a lie.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not tell you which of these is best. That is bakeoff.py's job, and
it needs market data this codebase's author never had access to. Nothing
here is ranked, recommended, or described as profitable until you run it.
"""
from __future__ import annotations


import config as C
import indicators as ind
import strategies as S
import strategy as base
from strategy import FLAT, LONG, SHORT


def _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd, now_utc,
        direction, rules, trigger):
    """Turn a list of (key, ok, points, max, text) into a finished result."""
    reasons, missing = [], []
    for key, ok, pts, mx, text in rules:
        reasons.append({"key": key, "ok": bool(ok), "points": float(pts),
                        "max": mx, "text": text})
        if not ok:
            missing.append(text)
    highs, lows = ind.last_swings(entry_df)
    in_sess = base._in_session(now_utc, spec)
    return S._finalise(out, direction=direction, reasons=reasons,
                       missing=missing, trigger_present=trigger, e=e,
                       entry_df=entry_df, highs=highs, lows=lows,
                       atr_e=float(e["atr"]), spec=spec, inst=inst,
                       balance=balance, risk_pct=risk_pct, risk_usd=risk_usd,
                       now_utc=now_utc, in_session=in_sess,
                       short_history=False)


def _prep(key, entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
          instrument, risk_usd):
    out, e, t, b, inst, balance, risk_pct, now_utc = S._scaffold(
        entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
        instrument, risk_usd, key)
    vetoes = S._common_vetoes(e, t, b, spec, now_utc, entry_df)
    return out, e, t, b, inst, balance, risk_pct, now_utc, vetoes


# --------------------------------------------------------------------------- #
#  4. MACD zero-line cross, with the higher timeframe behind it
# --------------------------------------------------------------------------- #
def macd_cross(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
               risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "macd_cross", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    hist = e["macd_hist"]
    if len(hist) < 3:
        vetoes.append("Not enough history for MACD")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    now, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    crossed_up = prev <= 0 < now
    crossed_dn = prev >= 0 > now
    bias_dir, _ = base._bias_direction(b)
    direction = LONG if crossed_up else SHORT if crossed_dn else (bias_dir or LONG)
    crossed = crossed_up or crossed_dn
    agrees = bias_dir == FLAT or direction == bias_dir
    trending = t["adx"] >= C.ADX_GOOD

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("macd_cross", crossed, 35 if crossed else 0, 35,
                    "MACD crossed its zero line" if crossed
                    else "MACD has not crossed zero"),
                   ("bias_agree", agrees, 25 if agrees else 0, 25,
                    "higher timeframe agrees" if agrees
                    else "cross fights the higher timeframe"),
                   ("adx", trending, 20 if trending else 8, 20,
                    f"{spec.trend_tf} ADX {t['adx']:.1f}"),
                   ("slope", abs(now) > abs(prev), 12 if abs(now) > abs(prev) else 0,
                    12, "histogram expanding" if abs(now) > abs(prev)
                    else "histogram flat"),
                   ("session", base._in_session(now_utc, spec),
                    8 if base._in_session(now_utc, spec) else 0, 8, "session"),
               ], crossed and agrees)


# --------------------------------------------------------------------------- #
#  5. RSI fade — only where there is demonstrably no trend
# --------------------------------------------------------------------------- #
def rsi_fade(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
             risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "rsi_fade", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    # Fading a trend is how accounts die. This ruleset only operates when ADX
    # says the market is ranging, and vetoes itself otherwise.
    if t["adx"] >= C.ADX_GOOD:
        vetoes.append(f"{spec.trend_tf} ADX {t['adx']:.1f} — trending, do not fade")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    r = float(e["rsi"].iloc[-1])
    oversold, overbought = r <= 30.0, r >= 70.0
    direction = LONG if oversold else SHORT if overbought else FLAT
    if direction == FLAT:
        out["vetoes"] = [f"RSI {r:.0f} — nothing to fade"]
        return out

    bb = ind.bollinger(entry_df["close"], 20, 2.0)
    close = float(e["close"])
    outside = (close < float(bb["lower"].iloc[-1]) if direction == LONG
               else close > float(bb["upper"].iloc[-1]))
    candle = e["candle"]
    rejects = candle.get("bull_pin") if direction == LONG else candle.get("bear_pin")

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("rsi_extreme", True, 30, 30, f"RSI {r:.0f} at an extreme"),
                   ("range", True, 20, 20, f"ADX {t['adx']:.1f} — ranging"),
                   ("band", outside, 25 if outside else 0, 25,
                    "closed outside the band" if outside else "still inside the band"),
                   ("rejection", rejects, 15 if rejects else 0, 15,
                    "rejection candle" if rejects else "no rejection candle yet"),
                   ("session", base._in_session(now_utc, spec),
                    10 if base._in_session(now_utc, spec) else 0, 10, "session"),
               ], bool(outside and rejects))


# --------------------------------------------------------------------------- #
#  6. EMA ribbon continuation
# --------------------------------------------------------------------------- #
def ribbon(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
           risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "ribbon", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    if vetoes:
        out["vetoes"] = vetoes
        return out

    stacked_up = e["ema20"] > e["ema50"] > e["ema_slow"]
    stacked_dn = e["ema20"] < e["ema50"] < e["ema_slow"]
    direction = LONG if stacked_up else SHORT if stacked_dn else FLAT
    if direction == FLAT:
        out["vetoes"] = ["EMAs are tangled — no ribbon"]
        return out

    close = float(e["close"])
    dip = (close <= e["ema20"] * 1.001 if direction == LONG
           else close >= e["ema20"] * 0.999)
    holds = (close > e["ema50"] if direction == LONG else close < e["ema50"])
    slope_ok = (e["ema20_slope"] > 0) if direction == LONG else (e["ema20_slope"] < 0)

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("stacked", True, 30, 30, "EMA ribbon fully stacked"),
                   ("dip", dip, 25 if dip else 0, 25,
                    "shallow dip into EMA20" if dip else "extended from EMA20"),
                   ("holds", holds, 20 if holds else 0, 20,
                    "holding above EMA50" if holds else "lost EMA50"),
                   ("slope", slope_ok, 15 if slope_ok else 0, 15,
                    "ribbon sloping with the trade" if slope_ok
                    else "ribbon flattening"),
                   ("session", base._in_session(now_utc, spec),
                    10 if base._in_session(now_utc, spec) else 0, 10, "session"),
               ], bool(dip and holds and slope_ok))


# --------------------------------------------------------------------------- #
#  7. Opening-range breakout
# --------------------------------------------------------------------------- #
def orb(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
        risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "orb", entry_df, trend_df, bias_df, spec, now_utc, balance, risk_pct,
        instrument, risk_usd)
    if not base._in_session(now_utc, spec):
        vetoes.append("Outside the session — no opening range")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    bars_per_hour = max(1, 3600 // base.TF_SECONDS.get(spec.entry_tf, 900))
    today = entry_df[entry_df.index.date == entry_df.index[-1].date()]
    if len(today) <= bars_per_hour:
        out["vetoes"] = ["Opening range not complete yet"]
        return out

    opening = today.iloc[:bars_per_hour]
    hi, lo = float(opening["high"].max()), float(opening["low"].min())
    close = float(e["close"])
    up, dn = close > hi, close < lo
    bias_dir, _ = base._bias_direction(b)
    direction = LONG if up else SHORT if dn else (bias_dir or LONG)
    broke = up or dn
    agrees = bias_dir == FLAT or direction == bias_dir

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("break", broke, 35 if broke else 0, 35,
                    "opening range broken" if broke else "inside the opening range"),
                   ("bias_agree", agrees, 25 if agrees else 0, 25,
                    "agrees with the higher timeframe" if agrees
                    else "fights the higher timeframe"),
                   ("adx", t["adx"] >= C.ADX_GATE,
                    20 if t["adx"] >= C.ADX_GATE else 5, 20,
                    f"{spec.trend_tf} ADX {t['adx']:.1f}"),
                   ("expansion", (e["atr_ratio"] or 1) >= 1.0,
                    12 if (e["atr_ratio"] or 1) >= 1.0 else 0, 12,
                    f"range {e['atr_ratio']:.2f}x normal"),
                   ("session", True, 8, 8, "inside session"),
               ], broke and agrees)


# --------------------------------------------------------------------------- #
#  8. Inside-bar compression
# --------------------------------------------------------------------------- #
def inside_bar(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
               risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "inside_bar", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    if len(entry_df) < 3:
        vetoes.append("Not enough bars")
    if vetoes:
        out["vetoes"] = vetoes
        return out

    prev, cur = entry_df.iloc[-2], entry_df.iloc[-1]
    inside = bool(cur["high"] <= prev["high"] and cur["low"] >= prev["low"])
    if not inside:
        out["vetoes"] = ["No inside bar"]
        return out

    bias_dir, _ = base._bias_direction(b)
    direction = bias_dir or (LONG if cur["close"] >= cur["open"] else SHORT)
    trending = t["adx"] >= C.ADX_GATE
    tight = float(cur["high"] - cur["low"]) < 0.6 * float(e["atr"])

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("inside", True, 30, 30, "inside bar — compression"),
                   ("bias", bias_dir != FLAT, 25 if bias_dir != FLAT else 0, 25,
                    "higher timeframe has a direction" if bias_dir != FLAT
                    else "no higher-timeframe direction"),
                   ("adx", trending, 20 if trending else 5, 20,
                    f"{spec.trend_tf} ADX {t['adx']:.1f}"),
                   ("tight", tight, 15 if tight else 0, 15,
                    "unusually tight bar" if tight else "not especially tight"),
                   ("session", base._in_session(now_utc, spec),
                    10 if base._in_session(now_utc, spec) else 0, 10, "session"),
               ], bool(inside and bias_dir != FLAT and trending))


# --------------------------------------------------------------------------- #
#  9. Pivot bounce
# --------------------------------------------------------------------------- #
def pivot_bounce(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
                 risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "pivot_bounce", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    if vetoes:
        out["vetoes"] = vetoes
        return out

    highs, lows = ind.last_swings(entry_df)
    close, atr = float(e["close"]), float(e["atr"])
    bias_dir, _ = base._bias_direction(b)
    direction = bias_dir or LONG

    level = None
    if direction == LONG and lows:
        level = lows[0][1]
    elif direction == SHORT and highs:
        level = highs[0][1]
    if level is None:
        out["vetoes"] = ["No swing level to react from"]
        return out

    near = abs(close - level) <= 0.5 * atr
    candle = e["candle"]
    rejects = candle.get("bull_pin") if direction == LONG else candle.get("bear_pin")
    trending = t["adx"] >= C.ADX_GATE

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("level", near, 30 if near else 0, 30,
                    "price at a prior swing" if near else "not near a swing"),
                   ("bias", bias_dir != FLAT, 25 if bias_dir != FLAT else 0, 25,
                    "higher timeframe direction present" if bias_dir != FLAT
                    else "no higher-timeframe direction"),
                   ("rejection", rejects, 25 if rejects else 0, 25,
                    "rejection candle" if rejects else "no rejection yet"),
                   ("adx", trending, 12 if trending else 4, 12,
                    f"{spec.trend_tf} ADX {t['adx']:.1f}"),
                   ("session", base._in_session(now_utc, spec),
                    8 if base._in_session(now_utc, spec) else 0, 8, "session"),
               ], bool(near and rejects))


# --------------------------------------------------------------------------- #
# 10. ADX expansion — a trend being born
# --------------------------------------------------------------------------- #
def adx_expand(entry_df, trend_df, bias_df, spec, now_utc=None, balance=None,
               risk_pct=None, instrument=None, risk_usd=None):
    out, e, t, b, inst, balance, risk_pct, now_utc, vetoes = _prep(
        "adx_expand", entry_df, trend_df, bias_df, spec, now_utc, balance,
        risk_pct, instrument, risk_usd)
    if vetoes:
        out["vetoes"] = vetoes
        return out

    now_adx, prev_adx = t["adx"], t["adx_prev"]
    crossed = prev_adx < C.ADX_GATE <= now_adx
    direction = LONG if t["plus_di"] > t["minus_di"] else SHORT
    bias_dir, _ = base._bias_direction(b)
    agrees = bias_dir == FLAT or direction == bias_dir
    di_gap = abs(t["plus_di"] - t["minus_di"])
    wide = di_gap >= 8.0

    return _mk(out, e, entry_df, spec, inst, balance, risk_pct, risk_usd,
               now_utc, direction, [
                   ("adx_cross", crossed, 32 if crossed else 0, 32,
                    f"ADX crossed up through {C.ADX_GATE:g}" if crossed
                    else f"ADX {now_adx:.1f} — no fresh cross"),
                   ("di_gap", wide, 25 if wide else 0, 25,
                    f"DI gap {di_gap:.1f}" if wide else f"DI gap only {di_gap:.1f}"),
                   ("bias_agree", agrees, 23 if agrees else 0, 23,
                    "agrees with the higher timeframe" if agrees
                    else "fights the higher timeframe"),
                   ("expansion", (e["atr_ratio"] or 1) >= 1.0,
                    12 if (e["atr_ratio"] or 1) >= 1.0 else 0, 12,
                    f"range {e['atr_ratio']:.2f}x normal"),
                   ("session", base._in_session(now_utc, spec),
                    8 if base._in_session(now_utc, spec) else 0, 8, "session"),
               ], crossed and agrees)


CANDIDATES: dict[str, dict] = {
    "ronin":        {"name": "Ronin Edge",     "fn": S._evaluate_ronin,
                     "idea": "trend pullback"},
    "crimson":      {"name": "Crimson Flow",   "fn": S.evaluate_crimson,
                     "idea": "channel breakout"},
    "kage":         {"name": "Kage Protocol",  "fn": S.evaluate_kage,
                     "idea": "volatility squeeze"},
    "macd_cross":   {"name": "MACD Zero Cross", "fn": macd_cross,
                     "idea": "momentum regime change"},
    "rsi_fade":     {"name": "RSI Fade",       "fn": rsi_fade,
                     "idea": "range mean reversion"},
    "ribbon":       {"name": "EMA Ribbon",     "fn": ribbon,
                     "idea": "trend continuation"},
    "orb":          {"name": "Opening Range",  "fn": orb,
                     "idea": "session breakout"},
    "inside_bar":   {"name": "Inside Bar",     "fn": inside_bar,
                     "idea": "compression break"},
    "pivot_bounce": {"name": "Pivot Bounce",   "fn": pivot_bounce,
                     "idea": "support/resistance reaction"},
    "adx_expand":   {"name": "ADX Expansion",  "fn": adx_expand,
                     "idea": "trend birth"},
}
