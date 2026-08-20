"""
MULTI-TIMEFRAME TREND-PULLBACK CONFLUENCE
=========================================

The logic in one paragraph: the highest timeframe holds a veto over direction,
the middle timeframe decides which way we lean and whether the market is even
trending, and the lowest timeframe supplies the trigger. We only ever buy dips
inside an uptrend or sell rallies inside a downtrend. Never breakouts, never
counter-trend, never in chop.

Nothing is scored until every hard gate passes. If a gate fails the answer is
NO TRADE regardless of how pretty the rest of the chart looks — that is the
whole point of a gate.

Three numbers come back and they are not interchangeable. The SCORE is rule
agreement. CONFIDENCE is that score after the hazards a scorecard cannot see —
news hours, dead sessions, no room to the next swing. PROBABILITY is the odds
of the target trading before the stop, from barrier maths plus whatever the
backtest has actually measured. See probability.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

import config as C
import indicators as ind
import instruments as I
import probability as prob

LONG, SHORT, FLAT = 1, -1, 0
DIR_NAME = {LONG: "BUY", SHORT: "SELL", FLAT: "NONE"}


# --------------------------------------------------------------------------- #
#  Per-timeframe snapshot
# --------------------------------------------------------------------------- #
def _slow_period(df: pd.DataFrame) -> int:
    """Weekly gold history is short. Degrade gracefully instead of returning NaN."""
    if len(df) >= 220:
        return 200
    if len(df) >= 130:
        return 100
    return max(60, len(df) // 2)


def snapshot(df: pd.DataFrame) -> dict:
    close = df["close"]
    slow_n = _slow_period(df)

    e20 = ind.ema(close, 20)
    e50 = ind.ema(close, 50)
    e_slow = ind.ema(close, slow_n)
    a = ind.atr(df, 14)
    adx_df = ind.adx(df, 14)
    macd_df = ind.macd(close)
    r = ind.rsi(close, 14)

    atr_now = float(a.iloc[-1])
    atr_median = float(a.tail(100).median()) if len(a) >= 30 else atr_now

    return {
        "df": df,
        "time": df.index[-1],
        "close": float(close.iloc[-1]),
        "ema20": float(e20.iloc[-1]),
        "ema50": float(e50.iloc[-1]),
        "ema_slow": float(e_slow.iloc[-1]),
        "ema_slow_n": slow_n,
        "ema20_slope": float(e20.iloc[-1] - e20.iloc[-4]) if len(e20) > 4 else 0.0,
        "atr": atr_now,
        "atr_median": atr_median,
        "atr_ratio": atr_now / atr_median if atr_median else 1.0,
        "adx": float(adx_df["adx"].iloc[-1]),
        "plus_di": float(adx_df["plus_di"].iloc[-1]),
        "minus_di": float(adx_df["minus_di"].iloc[-1]),
        "rsi": r,
        "macd_hist": macd_df["hist"],
        "candle": ind.candle_shape(df),
    }


def _direction_from_emas(s: dict) -> int:
    """Middle timeframe: which way are we leaning?"""
    up = s["ema20"] > s["ema50"] and s["close"] > s["ema50"] and s["plus_di"] > s["minus_di"]
    dn = s["ema20"] < s["ema50"] and s["close"] < s["ema50"] and s["minus_di"] > s["plus_di"]
    if up and not dn:
        return LONG
    if dn and not up:
        return SHORT
    return FLAT


def _bias_direction(s: dict) -> tuple[int, int]:
    """Highest timeframe veto. Returns (direction, strength) where strength is
    2 for a full EMA cross plus price agreement, 1 for price side only."""
    above_slow = s["close"] > s["ema_slow"]
    cross_up = s["ema50"] > s["ema_slow"]

    if above_slow and cross_up:
        return LONG, 2
    if (not above_slow) and (not cross_up):
        return SHORT, 2
    return (LONG, 1) if above_slow else (SHORT, 1)


# --------------------------------------------------------------------------- #
#  Pullback geometry
# --------------------------------------------------------------------------- #
def _impulse_retracement(df: pd.DataFrame, direction: int, lookback: int = 60):
    """How deep into the last impulse leg has price pulled back?
    0.0 = still at the extreme, 1.0 = leg fully erased."""
    win = df.tail(lookback)
    if len(win) < 12:
        return None, None, None

    price = float(win["close"].iloc[-1])

    if direction == LONG:
        hi_pos = int(win["high"].to_numpy().argmax())
        if hi_pos < 4:
            return None, None, None
        leg_high = float(win["high"].iloc[hi_pos])
        leg_low = float(win["low"].iloc[:hi_pos].min())
        leg = leg_high - leg_low
        if leg <= 0:
            return None, None, None
        return (leg_high - price) / leg, leg_low, leg_high

    lo_pos = int(win["low"].to_numpy().argmin())
    if lo_pos < 4:
        return None, None, None
    leg_low = float(win["low"].iloc[lo_pos])
    leg_high = float(win["high"].iloc[:lo_pos].max())
    leg = leg_high - leg_low
    if leg <= 0:
        return None, None, None
    return (price - leg_low) / leg, leg_low, leg_high


def _in_session(now: datetime, spec: C.ModeSpec) -> bool:
    minutes = now.hour * 60 + now.minute
    for sh, sm, eh, em in spec.sessions_utc:
        if sh * 60 + sm <= minutes <= eh * 60 + em:
            return True
    return False


# --------------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------------- #
def evaluate(
    entry_df: pd.DataFrame,
    trend_df: pd.DataFrame,
    bias_df: pd.DataFrame,
    spec: C.ModeSpec,
    now_utc: Optional[datetime] = None,
    balance: float = None,
    risk_pct: float = None,
    instrument: "I.Instrument" = None,
) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    # Sizing and rounding are per-instrument: a forex lot is 100,000 base
    # units against gold's 100 ounces, and EUR/USD needs five decimals where
    # gold needs two. Defaulting to gold keeps every older call site correct.
    inst = instrument or I.GOLD
    balance = C.ACCOUNT_BALANCE if balance is None else balance
    risk_pct = C.RISK_PCT if risk_pct is None else risk_pct

    e = snapshot(entry_df)
    t = snapshot(trend_df)
    b = snapshot(bias_df)

    price = e["close"]
    atr_e = e["atr"]

    out = {
        "mode": spec.name,
        "instrument": inst.key,
        "symbol": inst.symbol,
        "price": price,
        "as_of": e["time"],
        "timeframes": {"entry": spec.entry_tf, "trend": spec.trend_tf, "bias": spec.bias_tf},
        "decision": "NO TRADE",
        "direction": FLAT,
        "score": 0,
        "reasons": [],
        "vetoes": [],
        "missing": [],
        "levels": None,
        # A hard veto never gets scored, so it never gets probabilities either.
        # Zero confidence is the honest answer, not a missing key.
        "confidence": {"value": 0, "from_score": 0, "adjustments": [], "drag": 0},
        "probability": None,
    }

    trend_dir = _direction_from_emas(t)
    bias_dir, bias_strength = _bias_direction(b)

    # ------------------------------------------------------------------ GATES
    vetoes = []

    if trend_dir == FLAT:
        vetoes.append(f"{spec.trend_tf} has no clean trend (EMA20/50 tangled)")

    if trend_dir != FLAT and bias_strength == 2 and bias_dir != trend_dir:
        vetoes.append(
            f"{spec.bias_tf} bias is {DIR_NAME[bias_dir].lower()} — conflicts with "
            f"{spec.trend_tf} trend"
        )

    if t["adx"] < C.ADX_GATE:
        vetoes.append(f"{spec.trend_tf} ADX {t['adx']:.1f} — market is ranging, not trending")

    if e["atr_ratio"] > C.VOL_SPIKE_MULT:
        vetoes.append(
            f"Volatility spike ({e['atr_ratio']:.1f}x normal) — likely news, spreads blow out"
        )
    if e["atr_ratio"] < C.VOL_DEAD_MULT:
        vetoes.append(f"Dead tape ({e['atr_ratio']:.1f}x normal ATR) — nothing to trade")

    rsi_now = float(e["rsi"].iloc[-1])
    if trend_dir == LONG and rsi_now > C.RSI_OVERBOUGHT:
        vetoes.append(f"Entry RSI {rsi_now:.0f} — that is chasing, not a pullback")
    if trend_dir == SHORT and rsi_now < C.RSI_OVERSOLD:
        vetoes.append(f"Entry RSI {rsi_now:.0f} — that is chasing, not a pullback")

    age = (now_utc - e["time"].to_pydatetime()).total_seconds()
    tf_seconds = {"5min": 300, "15min": 900, "1h": 3600, "4h": 14400,
                  "1day": 86400, "1week": 604800}.get(spec.entry_tf, 900)
    if age > tf_seconds * 4:
        vetoes.append(f"Data is stale ({age / 60:.0f} min old) — market likely closed")

    if vetoes:
        out["vetoes"] = vetoes
        return out

    direction = trend_dir
    out["direction"] = direction

    # ----------------------------------------------------------------- SCORING
    reasons = []

    def add(key: str, earned: float, text: str):
        cap = C.WEIGHTS[key]
        earned = max(0.0, min(float(earned), cap))
        reasons.append({"key": key, "points": round(earned, 1), "max": cap,
                        "ok": earned >= cap * 0.6, "text": text})
        return earned

    # 1. Higher-timeframe bias
    if bias_dir == direction and bias_strength == 2:
        add("bias_align", 20,
            f"{spec.bias_tf} bias {DIR_NAME[direction].lower()}: "
            f"EMA50 {'>' if direction == LONG else '<'} EMA{b['ema_slow_n']}, price agrees"
            if b["ema_slow_n"] != 50 else
            f"{spec.bias_tf} bias {DIR_NAME[direction].lower()}, price agrees")
    elif bias_dir == direction:
        add("bias_align", 11,
            f"{spec.bias_tf} price on the right side of EMA{b['ema_slow_n']}, "
            f"but EMA50 has not crossed yet")
    else:
        add("bias_align", 0, f"{spec.bias_tf} bias is not confirming")

    # 2. Trend-timeframe EMA stack
    stacked = (
        t["ema20"] > t["ema50"] > t["ema_slow"] if direction == LONG
        else t["ema20"] < t["ema50"] < t["ema_slow"]
    )
    slope_ok = (t["ema20_slope"] > 0) if direction == LONG else (t["ema20_slope"] < 0)
    if stacked and slope_ok:
        add("trend_align", 15, f"{spec.trend_tf} EMA 20/50/{t['ema_slow_n']} fully stacked")
    elif stacked or slope_ok:
        add("trend_align", 8, f"{spec.trend_tf} trend present but EMAs not fully stacked")
    else:
        add("trend_align", 3, f"{spec.trend_tf} trend is weak")

    # 3. Trend strength
    adx_v = t["adx"]
    if adx_v >= C.ADX_STRONG:
        pts = 12
    elif adx_v >= C.ADX_GOOD:
        pts = 8 + 4 * (adx_v - C.ADX_GOOD) / (C.ADX_STRONG - C.ADX_GOOD)
    else:
        pts = 8 * (adx_v - C.ADX_GATE) / (C.ADX_GOOD - C.ADX_GATE)
    add("adx_strength", pts, f"{spec.trend_tf} ADX {adx_v:.1f}")

    # 4. Pullback quality — depth of retracement plus proximity to dynamic support
    retr, leg_low, leg_high = _impulse_retracement(entry_df, direction)
    dist_ema = abs(price - e["ema20"]) / atr_e if atr_e else 9.9

    depth_pts, depth_txt = 0.0, "no clean impulse leg to measure"
    if retr is not None:
        lo_i, hi_i = C.PULLBACK_IDEAL
        lo_a, hi_a = C.PULLBACK_ACCEPT
        if lo_i <= retr <= hi_i:
            depth_pts, depth_txt = 9, f"pullback {retr:.0%} of last leg (golden pocket)"
        elif lo_a <= retr <= hi_a:
            depth_pts, depth_txt = 5, f"pullback {retr:.0%} of last leg (acceptable)"
        elif retr < lo_a:
            depth_pts, depth_txt = 1, f"pullback only {retr:.0%} — barely pulled back"
        else:
            depth_pts, depth_txt = 0, f"pullback {retr:.0%} — leg mostly erased, trend at risk"

    prox_pts = 6 if dist_ema <= 0.8 else (3 if dist_ema <= 1.6 else 0)
    add("pullback_quality", depth_pts + prox_pts,
        f"{depth_txt}; {dist_ema:.1f} ATR from EMA20")

    # 5. Momentum trigger — must have fired within the last 3 closed bars
    rsi_s = e["rsi"]
    hist = e["macd_hist"]
    look = 3

    if direction == LONG:
        rsi_fired = any(
            rsi_s.iloc[-k - 1] < C.RSI_LONG_RECROSS <= rsi_s.iloc[-k]
            for k in range(1, look + 1)
        ) or (rsi_s.iloc[-1] > C.RSI_LONG_RECROSS and rsi_s.iloc[-1] > rsi_s.iloc[-2])
        macd_fired = any(
            hist.iloc[-k - 1] <= 0 < hist.iloc[-k] for k in range(1, look + 1)
        ) or (hist.iloc[-1] > hist.iloc[-2] and hist.iloc[-1] > 0)
    else:
        rsi_fired = any(
            rsi_s.iloc[-k - 1] > C.RSI_SHORT_RECROSS >= rsi_s.iloc[-k]
            for k in range(1, look + 1)
        ) or (rsi_s.iloc[-1] < C.RSI_SHORT_RECROSS and rsi_s.iloc[-1] < rsi_s.iloc[-2])
        macd_fired = any(
            hist.iloc[-k - 1] >= 0 > hist.iloc[-k] for k in range(1, look + 1)
        ) or (hist.iloc[-1] < hist.iloc[-2] and hist.iloc[-1] < 0)

    trigger_present = bool(rsi_fired or macd_fired)
    mom_pts = (6 if rsi_fired else 0) + (7 if macd_fired else 0)
    add("momentum_trigger", mom_pts,
        f"RSI {rsi_now:.0f} {'turned' if rsi_fired else 'flat/against'}, "
        f"MACD hist {'flipped ' + ('up' if direction == LONG else 'down') if macd_fired else 'not flipped'}")

    # 6. Candle confirmation on the last closed entry-TF bar
    ck = e["candle"]["kind"]
    want = "bullish" if direction == LONG else "bearish"
    if ck == f"{want}_engulfing":
        add("candle_confirm", 10, f"{ck.replace('_', ' ')} close")
    elif ck == f"{want}_pin":
        add("candle_confirm", 8, f"{ck.replace('_', ' ')} rejection")
    elif ck == f"{want}_marubozu":
        add("candle_confirm", 6, "strong-bodied continuation candle")
    elif (direction == LONG and e["candle"]["bullish"]) or (
        direction == SHORT and e["candle"]["bearish"]
    ):
        add("candle_confirm", 4, f"last candle closed {want} but unremarkable")
    else:
        add("candle_confirm", 0, "last candle closed against the trade")

    # 7. Market structure on the entry timeframe
    highs, lows = ind.last_swings(entry_df, 2, 2, count=2)
    struct_pts, struct_txt = 5, "structure unclear (not enough confirmed swings)"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[0][1] > highs[1][1]
        hl = lows[0][1] > lows[1][1]
        if direction == LONG:
            if hh and hl:
                struct_pts, struct_txt = 10, "higher highs and higher lows intact"
            elif hh or hl:
                struct_pts, struct_txt = 5, "structure only partly bullish"
            else:
                struct_pts, struct_txt = 0, "structure broken down (LH + LL)"
        else:
            if (not hh) and (not hl):
                struct_pts, struct_txt = 10, "lower highs and lower lows intact"
            elif (not hh) or (not hl):
                struct_pts, struct_txt = 5, "structure only partly bearish"
            else:
                struct_pts, struct_txt = 0, "structure broken up (HH + HL)"
    add("structure", struct_pts, struct_txt)

    # 8. Session and volatility regime
    in_sess = _in_session(now_utc, spec)
    sess_pts = (3 if in_sess else 0) + (2 if 0.7 <= e["atr_ratio"] <= 1.8 else 0)
    add("session_vol", sess_pts,
        f"{'inside' if in_sess else 'OUTSIDE'} {spec.name} session window, "
        f"ATR {e['atr_ratio']:.1f}x normal")

    score = sum(r["points"] for r in reasons)
    out["reasons"] = reasons
    out["score"] = int(round(score))
    out["missing"] = [r["text"] for r in reasons if not r["ok"]]

    # ------------------------------------------------------------------ LEVELS
    buffer = 0.35 * atr_e
    min_stop = spec.atr_sl_mult * atr_e

    if direction == LONG:
        struct_stop = lows[0][1] if lows else price - min_stop
        sl = min(struct_stop - buffer, price - min_stop)
        opposing = highs[0][1] if highs and highs[0][1] > price else None
    else:
        struct_stop = highs[0][1] if highs else price + min_stop
        sl = max(struct_stop + buffer, price + min_stop)
        opposing = lows[0][1] if lows and lows[0][1] < price else None

    risk_per_unit = abs(price - sl)
    tps = [
        price + direction * risk_per_unit * m for m in spec.tp_multiples
    ]

    room_rr = None
    if opposing is not None and risk_per_unit > 0:
        room_rr = abs(opposing - price) / risk_per_unit

    risk_cash = balance * risk_pct / 100.0
    d = inst.digits

    # Size from the levels as PRINTED, not the unrounded ones. Subtracting the
    # stop from the entry on screen has to give you the risk on screen, and the
    # lot size has to match that same number.
    entry_r, stop_r = round(float(price), d), round(float(sl), d)
    risk_shown = abs(entry_r - stop_r)

    # Risk per lot lands in the quote currency; convert before dividing, or a
    # 23,600-yen stop on USD/JPY reads as a 23,600-dollar stop and the size
    # collapses to zero.
    usd_per_quote = inst.usd_per_quote(entry_r)
    lots = None
    if risk_shown > 0 and usd_per_quote:
        risk_per_lot_usd = risk_shown * inst.contract_size * usd_per_quote
        if risk_per_lot_usd > 0:
            lots = risk_cash / risk_per_lot_usd
    out["levels"] = {
        "entry": entry_r,
        "stop": stop_r,
        "risk_points": round(risk_shown, d),
        "risk_display": inst.fmt_risk(risk_shown),
        "tps": [round(float(x), d) for x in tps],
        "tp_multiples": spec.tp_multiples,
        "room_rr": round(float(room_rr), 2) if room_rr else None,
        "next_obstacle": round(float(opposing), d) if opposing else None,
        # Small sizes need more than two decimals or a 0.074-lot gold trade
        # prints as 0.07 and quietly risks 5% less than you asked for.
        "lots": None if lots is None else round(float(lots), 2 if lots >= 1 else 3),
        "risk_cash": round(float(risk_cash), 2),
        "atr": round(float(atr_e), d),
    }

    # ---------------------------------------------------------------- DECISION
    momentum_pts = next(r["points"] for r in reasons if r["key"] == "momentum_trigger")
    candle_pts = next(r["points"] for r in reasons if r["key"] == "candle_confirm")
    quality_ok = trigger_present and momentum_pts >= 6 and candle_pts >= 4

    if score >= C.ENTRY_MIN_SCORE and quality_ok:
        if room_rr is not None and room_rr < spec.min_rr:
            out["decision"] = "WAIT"
            out["missing"].insert(
                0, f"only {room_rr:.1f}R of room to {out['levels']['next_obstacle']} "
                   f"(need {spec.min_rr}R)"
            )
        else:
            out["decision"] = "ENTRY"
    elif score >= C.WAIT_MIN_SCORE or (score >= 40 and quality_ok):
        out["decision"] = "WAIT"
        if not trigger_present:
            out["missing"].insert(0, "no momentum trigger yet — nothing has turned")
        elif not quality_ok:
            out["missing"].insert(0, "trigger is marginal — needs a cleaner confirming close")
    else:
        out["decision"] = "NO TRADE"

    news_hour = now_utc.hour in C.NEWS_WARNING_HOURS_UTC
    if news_hour:
        out["news_warning"] = True

    # ------------------------------------------------- CONFIDENCE + PROBABILITY
    # The scorecard measures agreement between indicators. These two measure
    # something the indicators cannot: how much the surrounding conditions
    # should discount that agreement, and what the resulting trade is worth.
    flags = {
        "outside_session": not in_sess,
        "news_hour": news_hour,
        "short_history": b["ema_slow_n"] < 200,
        "no_room": room_rr is not None and room_rr < spec.min_rr,
        "half_trigger": not (rsi_fired and macd_fired),
        "weak_structure": struct_pts < 10,
        "odd_volatility": not (0.7 <= e["atr_ratio"] <= 1.8),
        "ageing_data": age > tf_seconds * 1.5,
        "weak_candle": candle_pts < 6,
    }
    out["confidence"] = prob.confidence(
        out["score"], flags, clean_sweep=all(r["ok"] for r in reasons)
    )
    out["probability"] = prob.estimate(
        score=out["score"],
        targets_r=spec.tp_multiples,
        room_rr=room_rr,
        mode=spec.name,
    )

    return out
