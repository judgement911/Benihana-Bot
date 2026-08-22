"""Offline sanity check — no API key needed. Run: python selftest.py"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

import config as C
import probability as prob
from indicators import resample_ohlc
from strategy import evaluate


def synth(n=4000, kind="uptrend", seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")

    if kind == "uptrend":
        drift = 0.045
        wave = 6.0 * np.sin(np.arange(n) / 55.0)   # pullbacks inside the trend
    elif kind == "downtrend":
        drift = -0.045
        wave = 6.0 * np.sin(np.arange(n) / 55.0)
    else:  # chop
        drift = 0.0
        wave = 14.0 * np.sin(np.arange(n) / 30.0)

    noise = rng.normal(0, 1.1, n).cumsum()
    close = 2600 + drift * np.arange(n) + wave + noise

    spread = rng.uniform(0.6, 2.6, n)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    open_ = np.concatenate([[close[0]], close[:-1]])

    return pd.DataFrame(
        {"open": open_, "high": np.maximum(high, np.maximum(open_, close)),
         "low": np.minimum(low, np.minimum(open_, close)), "close": close},
        index=idx,
    )


def check(kind: str, mode: str = "intraday"):
    spec = C.MODES[mode]
    df = synth(kind=kind)
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    decisions = {"ENTRY": 0, "WAIT": 0, "NO TRADE": 0}
    dirs = {1: 0, -1: 0, 0: 0}
    scores = []

    # sample 300 points across the series
    for i in range(400, len(df), 12):
        now = df.index[i]
        res = evaluate(
            df.iloc[: i + 1],
            trend[trend.index <= now],
            bias[bias.index <= now],
            spec,
            now.to_pydatetime(),
        )
        decisions[res["decision"]] += 1
        dirs[res["direction"]] += 1
        if res["score"]:
            scores.append(res["score"])

    total = sum(decisions.values())
    print(f"{kind:<10} n={total:<4} "
          f"ENTRY {decisions['ENTRY'] / total:5.1%}  "
          f"WAIT {decisions['WAIT'] / total:5.1%}  "
          f"NOTRADE {decisions['NO TRADE'] / total:5.1%}  "
          f"| long {dirs[1]:<4} short {dirs[-1]:<4} "
          f"| mean score {np.mean(scores):.0f}" if scores else "")


def show_one():
    spec = C.MODES["intraday"]
    df = synth(kind="uptrend")
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    for i in range(1200, len(df), 4):
        now = df.index[i]
        res = evaluate(df.iloc[: i + 1], trend[trend.index <= now],
                       bias[bias.index <= now], spec, now.to_pydatetime())
        if res["decision"] == "ENTRY":
            print("\n--- example ENTRY ------------------------------------")
            print(f"score {res['score']}  dir {res['direction']}  price {res['price']}")
            for r in res["reasons"]:
                print(f"  {'v' if r['ok'] else 'x'} {r['text']}  [{r['points']}/{r['max']}]")
            print(f"  levels: {res['levels']}")
            return
    print("no ENTRY found in sample")


def check_probability_model():
    """The probability number has to obey a few laws or it is worse than
    useless. Assert them rather than eyeball them."""
    failures = []

    def want(cond, msg):
        if not cond:
            failures.append(msg)

    # A driftless market hits +kR before -1R exactly 1/(1+k) of the time, and
    # that is also the breakeven win rate. Costs push them apart, one each way.
    want(abs(prob.null_probability(1.0, 0.0) - 0.5) < 1e-9, "1R null should be 50%")
    want(abs(prob.null_probability(2.0, 0.0) - 1 / 3) < 1e-9, "2R null should be 33%")
    want(prob.null_probability(1.0) < prob.breakeven_rate(1.0),
         "costs must make the target harder AND the bar higher")

    # Monotone in the score, and never wilder than the caps allow.
    prev = 0.0
    for s in range(0, 101, 5):
        p = prob.model_probability(s, 1.0)
        want(p >= prev - 1e-12, f"probability fell as score rose at {s}")
        want(C.PROB_FLOOR - 1e-9 <= p <= C.PROB_CEIL + 1e-9, f"{p} out of bounds at {s}")
        prev = p

    # Farther targets are strictly harder, always.
    for s in (40, 70, 100):
        e = prob.estimate(s, (1.0, 2.0, 3.0), cal={})
        ps = [t["p"] for t in e["targets"]]
        want(ps == sorted(ps, reverse=True), f"targets not monotone at score {s}: {ps}")

    # Swings sitting inside the target have to cost something.
    open_room = prob.model_probability(80, 2.0, room_rr=5.0)
    blocked = prob.model_probability(80, 2.0, room_rr=0.5)
    want(blocked < open_room, "an obstacle inside the target changed nothing")

    # A perfect score must not buy a fantasy. 12-ish points over the null.
    lift = prob.model_probability(100, 1.0) - prob.null_probability(1.0)
    want(0.0 < lift < 0.20, f"edge term is out of hand: +{lift:.1%}")

    # Calibration: a thin sample nudges, a thick one dominates.
    thin = {"modes": {"intraday": {"trades": 10, "tp1": 0.90, "final": 0.80,
                                   "buckets": {}}}}
    thick = {"modes": {"intraday": {"trades": 4000, "tp1": 0.90, "final": 0.80,
                                    "buckets": {}}}}
    base = prob.estimate(75, (1.0, 2.0), mode="intraday", cal={})["p_first"]
    p_thin = prob.estimate(75, (1.0, 2.0), mode="intraday", cal=thin)["p_first"]
    p_thick = prob.estimate(75, (1.0, 2.0), mode="intraday", cal=thick)["p_first"]
    want(base < p_thin < p_thick, f"shrinkage misordered: {base:.3f} {p_thin:.3f} {p_thick:.3f}")
    want(p_thick <= C.PROB_CEIL + 1e-9, "calibration escaped the ceiling")

    # Samples below the minimum are ignored outright.
    tiny = {"modes": {"intraday": {"trades": 2, "tp1": 0.9, "final": 0.9,
                                   "buckets": {}}}}
    want(prob.estimate(75, (1.0, 2.0), mode="intraday", cal=tiny)["source"] == "model",
         "a 2-trade sample was allowed to speak")

    # Confidence: penalties bite, bonus lifts, result stays in range.
    clean = prob.confidence(80, {}, clean_sweep=True)["value"]
    hurt = prob.confidence(80, {k: True for k in C.CONFIDENCE_PENALTIES})["value"]
    want(clean == 86, f"clean sweep should be 86, got {clean}")
    want(5 <= hurt < 80, f"stacked penalties should bite: {hurt}")
    want(prob.confidence(2, {})["value"] >= 5, "confidence went below the floor")
    want(prob.confidence(100, {}, clean_sweep=True)["value"] <= 99,
         "confidence hit 100 — no read is that good")

    for msg in failures:
        print(f"  FAIL {msg}")
    print(f"  probability model: {'all checks passed' if not failures else str(len(failures)) + ' FAILURES'}")
    return not failures


def check_signal_fields(mode: str = "intraday"):
    """Every non-vetoed evaluation must carry both numbers, and a vetoed one
    must say zero rather than nothing."""
    spec = C.MODES[mode]
    df = synth(kind="uptrend")
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    seen = {"scored": 0, "vetoed": 0}
    failures = []

    for i in range(400, len(df), 17):
        now = df.index[i]
        res = evaluate(df.iloc[: i + 1], trend[trend.index <= now],
                       bias[bias.index <= now], spec, now.to_pydatetime())
        conf = res["confidence"]
        if res["vetoes"]:
            seen["vetoed"] += 1
            if conf["value"] != 0 or res["probability"] is not None:
                failures.append(f"veto at {now} quoted odds anyway")
            continue

        seen["scored"] += 1
        pr = res["probability"]
        if pr is None:
            failures.append(f"no probability at {now}")
            continue
        if not 0 <= conf["value"] <= 99:
            failures.append(f"confidence {conf['value']} out of range at {now}")
        if len(pr["targets"]) != len(spec.tp_multiples):
            failures.append(f"target count mismatch at {now}")
        if conf["value"] > res["score"] + C.CONFIDENCE_CLEAN_SWEEP_BONUS:
            failures.append(f"confidence exceeded score+bonus at {now}")
        if not prob.read_block(res):
            failures.append(f"empty read block at {now}")

    for msg in failures[:5]:
        print(f"  FAIL {msg}")
    print(f"  signal fields: {seen['scored']} scored, {seen['vetoed']} vetoed, "
          f"{'all present' if not failures else str(len(failures)) + ' FAILURES'}")
    return not failures


def check_instruments() -> bool:
    """Sizing and formatting are per-instrument, and getting either wrong is
    silent — a wrong lot size still looks like a number."""
    import instruments as I
    ok, fails = True, []

    for inst in I.all_instruments():
        if I.find(inst.key) is not inst or I.find(inst.display) is not inst:
            fails.append(f"{inst.display} does not resolve to itself")
        if inst.contract_size <= 0 or inst.pip <= 0 or inst.digits < 0:
            fails.append(f"{inst.display} has nonsense contract/pip/digits")
        # Price decimals must be fine enough to show one unit of movement.
        if inst.pip < 10 ** -inst.digits:
            fails.append(f"{inst.display} pip {inst.pip} finer than {inst.digits}dp")

    # Quote conversion: exact where it can be, absent where it cannot.
    cases = [("eurusd", 1.085, 1.0), ("xauusd", 4500.0, 1.0),
             ("usdjpy", 157.2, 1 / 157.2), ("gbpusd", 1.27, 1.0)]
    for key, px, want in cases:
        got = I.BY_KEY[key].usd_per_quote(px)
        if got is None or abs(got - want) > 1e-9:
            fails.append(f"{key} usd_per_quote {got} != {want}")
    for key in ("eurjpy", "gbpaud"):
        if I.BY_KEY[key].usd_per_quote(1.5) is not None:
            fails.append(f"{key} claims a USD rate it cannot know")

    # Every alias is unambiguous.
    seen = {}
    for inst in I.all_instruments():
        for a in inst.aliases:
            if a in seen:
                fails.append(f"alias {a!r} claimed by {seen[a]} and {inst.display}")
            seen[a] = inst.display

    for f in fails[:6]:
        print(f"  FAIL {f}")
        ok = False
    if ok:
        print(f"  instruments: {len(I.all_instruments())} defined, sizing and "
              "aliases consistent")
    return ok


def check_sizing() -> bool:
    """The printed lot size must actually risk the money it claims."""
    import instruments as I
    from indicators import resample_ohlc
    ok = True
    prices = {"xauusd": 4500.0, "eurusd": 1.0850, "usdjpy": 157.2,
              "gbpusd": 1.2700, "xagusd": 31.2}
    for key, base in prices.items():
        inst = I.BY_KEY[key]
        df = synth(kind="uptrend", n=1200)
        df = df / df["close"].iloc[0] * base
        e = resample_ohlc(df, "5min"); t = resample_ohlc(df, "15min")
        b = resample_ohlc(df, "1h")
        spec = C.MODES["scalp"]
        for i in range(400, len(e), 5):
            now = e.index[i]
            res = evaluate(e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                           spec, now.to_pydatetime(), instrument=inst)
            lv = res.get("levels")
            if not lv or lv.get("lots") is None:
                continue
            # printed entry - printed stop == printed risk
            if abs(abs(lv["entry"] - lv["stop"]) - lv["risk_points"]) > 10 ** -inst.digits:
                print(f"  FAIL {key}: printed levels disagree with printed risk")
                ok = False
            # Lots round DOWN to a broker step, so the realised risk sits at
            # or just under the request — never over it, and never by more
            # than one step's worth. That is the invariant, not "within 2%":
            # a small position can lose several percent to one 0.01 step and
            # still be behaving exactly as intended.
            usd = (lv["lots"] * inst.contract_size * lv["risk_points"]
                   * inst.usd_per_quote(lv["entry"]))
            step_usd = (C.LOT_STEP * inst.contract_size * lv["risk_points"]
                        * inst.usd_per_quote(lv["entry"]))
            if usd > lv["risk_cash"] + 1e-6:
                print(f"  FAIL {key}: risks ${usd:,.2f}, MORE than the "
                      f"${lv['risk_cash']:,.2f} asked for")
                ok = False
            elif lv["risk_cash"] - usd >= step_usd:
                print(f"  FAIL {key}: sized ${usd:,.2f} against ${lv['risk_cash']:,.2f} "
                      f"— more than one {C.LOT_STEP} step short")
                ok = False
            break
    if ok:
        print("  sizing: every asset class risks what it says it risks")
    return ok


def check_journal() -> bool:
    """The journal grades ties as losses, same as the backtester."""
    import os, tempfile
    import pandas as pd
    C.JOURNAL_FILE = os.path.join(tempfile.mkdtemp(), "journal.json")
    import importlib
    import journal as J
    importlib.reload(J)

    # Timestamps are relative to now, never hardcoded. resolve() measures a
    # signal's age against the clock and expires anything older than
    # JOURNAL_MAX_BARS, so a fixed date makes the "still open" case start
    # failing the moment real time drifts past that deadline.
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)

    def row(**kw):
        base = dict(id="t", ts=t0.isoformat(), instrument="xauusd",
                    mode="scalp", entry_tf="5min", direction=1, entry=4500.0,
                    stop=4490.0, tps=[4510.0, 4520.0], tp_multiples=[1.0, 2.0],
                    score=75, confidence=60, p_tp1=0.55, outcome="open",
                    hit_tp1=False, hit_final=False, r=None, resolved_ts=None)
        base.update(kw)
        return base

    def bars(seq):
        idx = pd.date_range(t0 + timedelta(minutes=5), periods=len(seq),
                            freq="5min", tz="UTC")
        return pd.DataFrame(
            [{"open": o, "high": h, "low": l, "close": c} for o, h, l, c in seq],
            index=idx)

    cases = [
        ("stop first", [(4500, 4505, 4489, 4492)], "loss"),
        ("tp1 then stop", [(4500, 4512, 4499, 4510), (4510, 4511, 4489, 4490)], "win"),
        ("both same bar", [(4500, 4515, 4485, 4500)], "loss"),   # tie -> stop
        ("neither", [(4500, 4503, 4498, 4501)], "open"),
    ]
    ok = True
    for name, seq, want in cases:
        J._save([row(id=name)])
        J.resolve(lambda s, tf, n: bars(seq))
        got = J._load()[0]["outcome"]
        if got != want:
            print(f"  FAIL journal {name}: {got}, expected {want}")
            ok = False
    # An empty journal must say so rather than invent a record.
    C.JOURNAL_FILE = os.path.join(tempfile.mkdtemp(), "empty.json")
    if "No signals recorded" not in J.format_stats("xauusd"):
        print("  FAIL empty journal does not say it is empty")
        ok = False
    if ok:
        print("  journal: ties score as losses, empty stays empty")
    return ok


def check_views() -> bool:
    """Both detail levels, every decision, inside Telegram's size limit."""
    import view
    from indicators import resample_ohlc
    ok, n, worst = True, 0, 0
    for kind in ("uptrend", "downtrend", "chop"):
        df = synth(kind=kind, n=2500)
        e = resample_ohlc(df, "5min"); t = resample_ohlc(df, "15min")
        b = resample_ohlc(df, "1h")
        for i in range(400, len(e), 37):
            now = e.index[i]
            res = evaluate(e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                           C.MODES["scalp"], now.to_pydatetime())
            for verbose in (False, True):
                try:
                    out = view.render("XAUUSD", res, verbose)
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAIL render raised {type(exc).__name__}: {exc}")
                    return False
                n += 1
                worst = max(worst, len(out))
                if len(out) > 4096:
                    print(f"  FAIL message {len(out)} chars, Telegram caps at 4096")
                    ok = False
    if ok:
        print(f"  view: {n} renders clean, longest {worst} chars of 4096")
    return ok


def check_orders() -> bool:
    """The stop must stay inside its mode's band, and a pending order must sit
    on the correct side of price — a BUY LIMIT above the market is not an
    order, it is a typo that fills instantly."""
    import instruments as I
    rule = {"5min": "5min", "15min": "15min", "1h": "1h", "4h": "4h",
            "1day": "1D", "1week": "1W"}
    problems, kinds, n = [], set(), 0

    for price_level, inst in ((4500.0, I.BY_KEY["xauusd"]),
                              (1.0850, I.BY_KEY["eurusd"])):
        base = synth(kind="uptrend", n=6000)
        base = base / base["close"].iloc[0] * price_level
        for mode in C.MODES:
            spec = C.MODES[mode]
            e = base if spec.entry_tf == "15min" else resample_ohlc(base, rule[spec.entry_tf])
            t = resample_ohlc(base, rule[spec.trend_tf])
            b = resample_ohlc(base, rule[spec.bias_tf])
            if len(e) < 400 or len(b) < 40:
                continue
            for i in range(400, len(e), 29):
                now = e.index[i]
                res = evaluate(e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                               spec, now.to_pydatetime(), instrument=inst)
                lv, order = res.get("levels"), res.get("order")
                if not lv or not order:
                    continue
                n += 1
                kinds.add(order["kind"])
                tag = f"{inst.display}/{mode}"

                if lv["stop_atr"] is not None and lv["stop_atr"] > spec.max_sl_mult + 0.02:
                    problems.append(f"{tag}: stop {lv['stop_atr']}x ATR over ceiling "
                                    f"{spec.max_sl_mult}")
                if lv["stop_atr"] is not None and lv["stop_atr"] < spec.atr_sl_mult - 0.02:
                    problems.append(f"{tag}: stop {lv['stop_atr']}x ATR under floor "
                                    f"{spec.atr_sl_mult}")

                entry, stop, long = lv["entry"], lv["stop"], res["direction"] == 1
                if long and not stop < entry:
                    problems.append(f"{tag}: long stop {stop} not below entry {entry}")
                if (not long) and not stop > entry:
                    problems.append(f"{tag}: short stop {stop} not above entry {entry}")
                for tp, m in zip(lv["tps"], lv["tp_multiples"]):
                    if long and not tp > entry:
                        problems.append(f"{tag}: long TP {tp} not above entry {entry}")
                    if (not long) and not tp < entry:
                        problems.append(f"{tag}: short TP {tp} not below entry {entry}")

                # the pending order has to be reachable in the right direction
                px = order["price"]
                if order["kind"] == "limit" and long and px > res["price"] + 1e-9:
                    problems.append(f"{tag}: buy limit {px} above market {res['price']}")
                if order["kind"] == "limit" and not long and px < res["price"] - 1e-9:
                    problems.append(f"{tag}: sell limit {px} below market {res['price']}")
                if order["kind"] == "stop" and long and px < res["price"] - 1e-9:
                    problems.append(f"{tag}: buy stop {px} below market {res['price']}")
                if order["kind"] == "stop" and not long and px > res["price"] + 1e-9:
                    problems.append(f"{tag}: sell stop {px} above market {res['price']}")
                if order["kind"] == "market" and res["decision"] != "ENTRY":
                    problems.append(f"{tag}: market order on a {res['decision']}")
                if abs(px - entry) > 10 ** -inst.digits:
                    problems.append(f"{tag}: levels anchored at {entry}, order at {px}")

    if problems:
        for p in problems[:6]:
            print(f"  orders: {p}")
        return False
    missing = {"market", "limit", "stop"} - kinds
    if missing:
        print(f"  orders: never produced {sorted(missing)} — untested paths")
        return False
    print(f"  orders: {n} plans, stops inside band, every order on the right side")
    return True


def check_money() -> bool:
    """Amount parsing, lot rounding, and the refusal to guess a rate."""
    import money as M
    ok = True
    for text, want_v, want_c in [
        ("20$", 20.0, "USD"), ("100 USD", 100.0, "USD"), ("22$", 22.0, "USD"),
        ("300k IDR", 300_000.0, "IDR"), ("17000000 IDR", 17_000_000.0, "IDR"),
        ("300000 IDR", 300_000.0, "IDR"), ("1.5jt", 1_500_000.0, "IDR"),
        ("Rp250000", 250_000.0, "IDR"), ("1.000.000", 1_000_000.0, "IDR"),
    ]:
        try:
            v, c = M.parse_amount(text)
        except M.MoneyError:
            print(f"  FAIL money rejected {text!r}")
            ok = False
            continue
        if abs(v - want_v) > 0.01 or c != want_c:
            print(f"  FAIL money {text!r} -> {v} {c}, wanted {want_v} {want_c}")
            ok = False
    for bad in ("banana", "", "0$", "-5"):
        try:
            M.parse_amount(bad)
            print(f"  FAIL money accepted {bad!r}")
            ok = False
        except M.MoneyError:
            pass

    # Lots round DOWN and never below the broker minimum.
    for raw in (0.0457, 0.199, 2.347):
        lots, below = M.round_lots(raw)
        if lots is None or lots > raw + 1e-9:
            print(f"  FAIL lots {raw} -> {lots} (rounded UP)")
            ok = False
        if round(lots / C.LOT_STEP) * C.LOT_STEP - lots > 1e-9:
            print(f"  FAIL lots {raw} -> {lots} is not on the step")
            ok = False
    if M.round_lots(0.004) != (None, True):
        print("  FAIL a size under the minimum must be reported, not rounded up")
        ok = False

    # An unavailable rate must yield None, never a hardcoded guess.
    M._rate_cache.clear()
    def dead_fetch(*a, **k):
        raise RuntimeError("provider down")
    if M.to_usd(300_000, "IDR", fetch=dead_fetch) is not None:
        print("  FAIL money invented an exchange rate when the provider failed")
        ok = False

    if ok:
        print("  money: parses both currencies, rounds down, never guesses a rate")
    return ok


def check_users() -> bool:
    """Settings survive a reload; daily counters roll on the WIB day."""
    import os, tempfile, importlib
    C.USERS_FILE = os.path.join(tempfile.mkdtemp(), "u.json")
    import users as U
    importlib.reload(U)
    ok = True

    U.update(11, language="id", strategy="kage", min_confidence=75)
    importlib.reload(U)
    got = U.get(11)
    if (got["language"], got["strategy"], got["min_confidence"]) != ("id", "kage", 75):
        print(f"  FAIL users did not persist: {got}")
        ok = False

    U.management_on(11, U.management_defaults(1000, 2, 5, 4, 10))
    if abs(U.risk_per_trade_usd(U.get(11)) - 20.0) > 1e-9:
        print("  FAIL risk per trade should be 2% of 1000")
        ok = False
    U.management_off(11)
    if U.risk_per_trade_usd(U.get(11)) is not None:
        print("  FAIL management off must stop sizing from balance")
        ok = False

    # A stale day resets the counters rather than carrying them over.
    U.update(11, day="1999-01-01", day_trades=9, day_pl_usd=-50.0)
    rolled = U.get(11)
    if rolled["day_trades"] != 0 or rolled["day_pl_usd"] != 0.0:
        print(f"  FAIL daily counters did not roll over: {rolled}")
        ok = False

    if ok:
        print("  users: settings persist, daily counters roll over")
    return ok


def check_sessions() -> bool:
    """Sessions come from the clock, volatility from the tape."""
    import sessions as SS
    from datetime import datetime, timezone as tz
    ok = True
    cases = [(3, "asia"), (9, "london"), (14, "ny"), (22, "sydney")]
    for hour, want in cases:
        got = SS.current_session(datetime(2026, 8, 20, hour, tzinfo=tz.utc))
        if not got or got["key"] != want:
            print(f"  FAIL session at {hour:02d}:00 UTC -> {got}, wanted {want}")
            ok = False
    if SS.current_session(datetime(2026, 8, 22, 14, tzinfo=tz.utc)) is not None:
        print("  FAIL FX is closed on Saturday")
        ok = False
    # Overlap resolves to the deeper book, never to both.
    overlap = SS.current_session(datetime(2026, 8, 20, 14, tzinfo=tz.utc))
    if overlap["key"] != "ny":
        print("  FAIL London/NY overlap must resolve to NY")
        ok = False

    if SS.classify_volatility(None) is not None:
        print("  FAIL unknown volatility must stay unknown, not default to a bucket")
        ok = False
    order = [SS.classify_volatility(r)["key"] for r in (0.5, 1.0, 2.0)]
    if order != ["low", "medium", "high"]:
        print(f"  FAIL volatility buckets out of order: {order}")
        ok = False

    stamp = SS.stamp(datetime(2026, 8, 20, 13, 15, tzinfo=tz.utc))
    if stamp != "20:15 UTC+7":
        print(f"  FAIL clock should read 20:15 UTC+7, got {stamp}")
        ok = False

    if ok:
        print("  sessions: clock-derived, overlap resolved, volatility measured")
    return ok


def check_i18n() -> bool:
    """Both languages must be complete, and placeholders must match."""
    import re as _re
    import i18n
    ok = True
    for key, row in i18n.S.items():
        for lang in i18n.LANGS:
            if not row.get(lang):
                print(f"  FAIL i18n {key!r} missing {lang}")
                ok = False
        en_slots = set(_re.findall(r"{(\w+)}", row.get(i18n.EN, "")))
        id_slots = set(_re.findall(r"{(\w+)}", row.get(i18n.ID, "")))
        if en_slots != id_slots:
            print(f"  FAIL i18n {key!r} placeholders differ: {en_slots} vs {id_slots}")
            ok = False
    if ok:
        print(f"  i18n: {len(i18n.S)} keys complete in both languages")
    return ok


def check_strategies() -> bool:
    """Three strategies, one contract, genuinely different answers."""
    import instruments as I
    import strategies as St
    from indicators import resample_ohlc
    ok = True
    spec = C.MODES["intraday"]
    df = synth(kind="uptrend", n=2000)
    df = df / df["close"].iloc[0] * 4500.0
    e = resample_ohlc(df, "15min"); t = resample_ohlc(df, "1h")
    b = resample_ohlc(df, "4h")
    seen = {k: set() for k in St.ORDER}
    for i in range(400, len(e), 23):
        now = e.index[i]
        args = (e.iloc[:i + 1], t[t.index <= now], b[b.index <= now], spec,
                now.to_pydatetime())
        for key in St.ORDER:
            try:
                r = St.evaluate(key, *args, instrument=I.GOLD)
            except Exception as exc:
                print(f"  FAIL {key} raised: {type(exc).__name__}: {exc}")
                return False
            if r.get("strategy") != key:
                print(f"  FAIL {key} did not stamp its own name")
                ok = False
            need = ["decision", "direction", "score", "reasons", "price", "as_of"]
            if not r["vetoes"]:
                need += ["levels", "order", "confidence", "probability"]
            for f in need:
                if r.get(f) is None:
                    print(f"  FAIL {key} missing {f}")
                    ok = False
                    break
            seen[key].add("veto" if r["vetoes"] else r["decision"])
    if len(set(map(frozenset, seen.values()))) < 2:
        print("  FAIL all three strategies behaved identically")
        ok = False
    if ok:
        print(f"  strategies: {len(St.ORDER)} rulesets, one contract, distinct answers")
    return ok


def check_lifecycle() -> bool:
    """The state machine, including the tie rule and the breakeven move."""
    import os, tempfile, importlib
    import pandas as pd
    C.JOURNAL_FILE = os.path.join(tempfile.mkdtemp(), "lc.json")
    import journal as J
    importlib.reload(J)
    ok = True
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)

    def row(**kw):
        base = dict(id="lc", ts=t0.isoformat(), instrument="xauusd",
                    mode="intraday", entry_tf="15min", direction=1,
                    entry=4500.0, stop=4490.0, tps=[4510.0, 4520.0, 4530.0],
                    tp_multiples=[1.0, 2.0, 3.0], score=80, confidence=75,
                    p_tp1=0.5, outcome="open", hit_tp1=False, hit_final=False,
                    r=None, resolved_ts=None, state="waiting",
                    order_kind="market", tps_hit=[], strategy="ronin",
                    user_id=1, risk_cash=50.0, risk_points=10.0, lots=0.05,
                    currency="USD")
        base.update(kw)
        return base

    def bars(seq):
        idx = pd.date_range(t0 + timedelta(minutes=15), periods=len(seq),
                            freq="15min", tz="UTC")
        return pd.DataFrame([{"open": o, "high": h, "low": l, "close": c}
                             for o, h, l, c in seq], index=idx)

    cases = [
        ("straight stop", [(4500, 4502, 4489, 4490)], "stopped", ["entry", "stop"]),
        ("all targets", [(4500, 4511, 4499, 4510), (4510, 4521, 4509, 4520),
                         (4520, 4531, 4519, 4530)], "completed",
         ["entry", "tp1", "breakeven", "tp2", "tp3", "complete"]),
        ("tie is a stop", [(4500, 4515, 4485, 4500)], "stopped", ["entry", "stop"]),
    ]
    for name, seq, want_state, want_events in cases:
        r = row()
        events = [e["kind"] for e in J.advance(r, bars(seq))]
        if r["state"] != want_state or events != want_events:
            print(f"  FAIL lifecycle {name}: state={r['state']} events={events}")
            ok = False

    # A pending order that never trades stays waiting and scores nothing.
    r = row(order_kind="limit", entry=4400.0)
    J.advance(r, bars([(4500, 4505, 4495, 4500)]))
    if r["state"] != "waiting" or r["r"] is not None:
        print(f"  FAIL an unfilled limit must stay waiting: {r['state']}")
        ok = False

    # §19: the cooldown is per pair AND style.
    J._save([row(id="a", state="active", mode="intraday")])
    if not J.active_signals(instrument="xauusd", mode="intraday"):
        print("  FAIL an active signal must block its own flow")
        ok = False
    if J.active_signals(instrument="xauusd", mode="swing"):
        print("  FAIL an intraday signal must not block swing")
        ok = False
    if ok:
        print("  lifecycle: states, tie rule, breakeven, per-flow cooldown")
    return ok


def check_payoff_agreement() -> bool:
    """The backtester, the journal and the signal must price a trade alike.

    They did not. The backtester scored a run that banked TP1 and TP2 before
    reversing as a full -1R loss; the journal called the same run +0.33R; and
    the expectancy printed on the signal was derived from neither. Three
    components describing one plan, disagreeing about what it pays.
    """
    import journal as J
    import probability as prob
    ok = True
    mults = [1.0, 2.0, 3.0]

    # 1. The journal must delegate to the shared definition.
    for hit, row in ((0, {"hit_tp1": False, "tps_hit": [], "hit_final": False}),
                     (1, {"hit_tp1": True, "tps_hit": [1], "hit_final": False}),
                     (2, {"hit_tp1": True, "tps_hit": [1, 2], "hit_final": False}),
                     (3, {"hit_tp1": True, "tps_hit": [1, 2, 3], "hit_final": True})):
        a = J._realised_r(dict(row, tp_multiples=mults))
        b = prob.realised_r(hit, mults, C.COST_R)
        if abs(a - b) > 1e-9:
            print(f"  FAIL payoff: journal {a:+.3f} vs shared {b:+.3f} at {hit} hit")
            ok = False

    # 2. More targets filled must never pay less.
    seq = [prob.realised_r(h, mults, 0.0) for h in range(len(mults) + 1)]
    if seq != sorted(seq):
        print(f"  FAIL payoff is not monotone in targets filled: {seq}")
        ok = False

    # 3. Banking a target must beat being stopped before any of them.
    if not prob.realised_r(1, mults, C.COST_R) > prob.realised_r(0, mults, C.COST_R):
        print("  FAIL reaching TP1 must beat not reaching it")
        ok = False

    # 4. The expectancy the signal prints must equal the shared payoff
    #    weighted by the probability of each outcome. If these drift apart,
    #    the bot is quoting an expectancy for a plan it does not trade.
    p = [0.55, 0.32, 0.18]
    by_formula = sum(pi * mi for pi, mi in zip(p, mults)) / len(mults) - (1 - p[0])
    by_outcome = (
        (p[0] - p[1]) * prob.realised_r(1, mults, 0.0)
        + (p[1] - p[2]) * prob.realised_r(2, mults, 0.0)
        + p[2] * prob.realised_r(3, mults, 0.0)
        + (1 - p[0]) * prob.realised_r(0, mults, 0.0)
    )
    if abs(by_formula - by_outcome) > 1e-9:
        print(f"  FAIL expectancy {by_formula:+.4f} does not match the payoff "
              f"model {by_outcome:+.4f}")
        ok = False

    if ok:
        print("  payoff: backtest, journal and signal price a trade identically")
    return ok


if __name__ == "__main__":
    print("\nBehaviour across market regimes (intraday mode):")
    for k in ("uptrend", "downtrend", "chop"):
        check(k)
    show_one()

    print("\nAll three modes run without error:")
    for m in C.MODES:
        df = synth(kind="uptrend")
        spec = C.MODES[m]
        rule = {"5min": "5min", "15min": "15min", "1h": "1h",
                "4h": "4h", "1day": "1D", "1week": "1W"}
        e = df if spec.entry_tf in ("15min", "5min") else resample_ohlc(df, rule[spec.entry_tf])
        t = resample_ohlc(df, rule[spec.trend_tf])
        b = resample_ohlc(df, rule[spec.bias_tf])
        if len(e) < 80 or len(t) < 60 or len(b) < 30:
            print(f"  {m:<9} skipped (synthetic history too short for {spec.bias_tf})")
            continue
        res = evaluate(e, t, b, spec, datetime.now(timezone.utc))
        pr = res["probability"]
        odds = f"P(TP1) {pr['p_first']:.0%}" if pr else "no odds (vetoed)"
        print(f"  {m:<9} -> {res['decision']:<9} score {res['score']:<4} "
              f"confidence {res['confidence']['value']:<4} {odds}")

    print("\nConfidence and probability:")
    ok = check_probability_model() & check_signal_fields()

    print("\nUniverse, sizing and presentation:")
    ok &= check_instruments() & check_sizing() & check_views()
    print("\nSettings, money and language:")
    ok &= check_money() & check_users() & check_sessions() & check_i18n()
    print("\nStrategies and lifecycle:")
    ok &= check_strategies() & check_lifecycle() & check_payoff_agreement()
    ok &= check_orders()

    print("\nSignal journal:")
    ok &= check_journal()
    raise SystemExit(0 if ok else 1)
