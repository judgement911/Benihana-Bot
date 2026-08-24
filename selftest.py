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



def _inspect_bodies(bodies, label, failures) -> bool:
    """Every reply must survive Telegram: short enough, and valid HTML."""
    import html as _html
    import re as _re
    ok = True
    for body in bodies:
        if len(body) > 4096:
            failures.append(f"{label} replied {len(body)} chars (max 4096)")
            ok = False
        # Telegram rejects the whole message on a malformed tag, so an
        # unescaped stray '<' is a delivery failure, not a cosmetic one.
        stripped = _re.sub(r"</?(b|i|u|s|code|pre|a|tg-spoiler|blockquote)"
                           r"(\s[^>]*)?>", "", body)
        if "<" in stripped or ">" in stripped:
            failures.append(f"{label} has raw angle brackets")
            ok = False
        if _html.unescape(body).strip() == "":
            failures.append(f"{label} replied with only markup")
            ok = False
    return ok


def check_resolve_robustness() -> bool:
    """One malformed row must not stop every other signal from settling.

    resolve() indexed r["entry_tf"] directly. A record written by an older
    version of the bot does not have it, and journal.json survives every
    deploy — so one stale row threw before anything was settled. Every
    caller catches and logs that exception, which means the user saw no
    error at all: /stats, /daily, /history and /update simply stayed empty
    for good.
    """
    from datetime import datetime, timedelta, timezone
    import journal as J

    rows = J._load()
    before = len(rows)
    now = datetime.now(timezone.utc)
    base_row = {
        "user_id": 90910, "instrument": "xauusd", "mode": "scalp",
        "entry_tf": "5min", "strategy": "kage", "direction": 1,
        "entry": 4500.0, "stop": 4490.0, "tps": [4510.0, 4520.0, 4530.0],
        "tps_hit": [], "hit_tp1": False, "state": J.ACTIVE,
        "outcome": J.OPEN, "risk_cash": 50.0,
        "ts": (now - timedelta(hours=3)).isoformat(),
    }
    good = {**base_row, "id": "rb_good"}
    legacy = {**base_row, "id": "rb_legacy"}
    legacy.pop("entry_tf")                       # recoverable from mode
    broken = {**base_row, "id": "rb_broken"}
    broken.pop("entry_tf")
    broken.pop("mode")                           # not recoverable, must skip
    J._save(rows + [good, legacy, broken])

    idx = pd.date_range(now - timedelta(hours=3), periods=60, freq="5min",
                        tz="UTC")
    c = np.linspace(4500.0, 4535.0, 60)
    frame = pd.DataFrame({"open": c, "high": c + 2, "low": c - 2, "close": c},
                         index=idx)

    ok = True
    try:
        out = J.resolve(lambda *a, **k: frame)
    except Exception as exc:                      # noqa: BLE001
        print(f"  x one malformed record killed the whole resolve pass: "
              f"{type(exc).__name__}: {exc}")
        J._save([r for r in J._load() if r.get("user_id") != 90910])
        return False

    settled = {r["id"]: r for r in J._load() if r.get("user_id") == 90910}
    if settled["rb_good"].get("r") is None:
        print("  x a well-formed signal did not settle")
        ok = False
    if settled["rb_legacy"].get("r") is None:
        print("  x a record missing entry_tf was not recovered from its mode")
        ok = False
    if out.get("skipped") != 1:
        print(f"  x expected 1 unrecoverable record skipped, got "
              f"{out.get('skipped')}")
        ok = False

    J._save([r for r in J._load() if r.get("user_id") != 90910])
    if len(J._load()) != before:
        print("  x the test left rows behind")
        ok = False
    if ok:
        print("  resolve: a malformed record is skipped, everything else "
              "still settles")
    return ok


def check_webhook_retry() -> bool:
    """A failed update must stay retryable, and must not be silent.

    The finally block marked every update seen whether handling had
    succeeded or not, so a transient failure discarded Telegram's retry as a
    duplicate — the command vanished and the one delivery that could have
    worked was thrown away. The user saw an empty chat and the traceback
    went to a log file nobody reads.
    """
    import config as _C
    import flask_app as F

    real_send, real_tg, real_allowed, real_handle = (
        F.send, F.tg, F.allowed, F.handle_message)
    sent = []
    F.send = lambda c, t, **k: sent.append(t)
    F.tg = lambda m, **p: (sent.append(p.get("text", ""))
                           if m == "sendMessage" else None) or {
                               "ok": True, "result": {"message_id": 1}}
    F.allowed = lambda uid: True
    F._seen_updates.clear()
    F._inflight.clear()
    client = F.app.test_client()
    ok = True

    def post(uid):
        return client.post(f"/webhook/{_C.WEBHOOK_SECRET}", json={
            "update_id": uid,
            "message": {"chat": {"id": 1}, "from": {"id": 5},
                        "text": "/whoami"}})

    try:
        if client.post("/webhook/definitely-wrong", json={}).status_code != 403:
            print("  x the webhook accepted a bad secret")
            ok = False

        sent.clear()
        post(8001)
        if not sent:
            print("  x a normal update produced no reply")
            ok = False
        sent.clear()
        post(8001)
        if sent:
            print("  x a duplicate update was answered twice")
            ok = False

        # A handler that raises: the user must hear about it, and the retry
        # must still be allowed through.
        F.handle_message = lambda m: (_ for _ in ()).throw(
            RuntimeError("simulated worker failure"))
        sent.clear()
        post(8002)
        if not sent:
            print("  x a failed update told the user nothing at all")
            ok = False
        F.handle_message = real_handle
        sent.clear()
        post(8002)
        if not sent:
            print("  x the retry after a failure was discarded as a duplicate "
                  "— the command is lost for good")
            ok = False
        sent.clear()
        post(8002)
        if sent:
            print("  x once it succeeded, a further duplicate was answered again")
            ok = False
    finally:
        F.send, F.tg, F.allowed, F.handle_message = (
            real_send, real_tg, real_allowed, real_handle)
        F._seen_updates.clear()
        F._inflight.clear()

    if ok:
        print("  webhook: duplicates deduped, failures reported and still "
              "retryable")
    return ok


def check_commands() -> bool:
    """Drive every offline command through the real dispatcher.

    This exists because a handler can be deleted, renamed or left referencing
    a helper that was never written, and nothing else in this suite would
    notice — the bot would import fine and then fail in the user's chat. So
    the assertion is deliberately end-to-end: build a message, hand it to
    handle_message, and demand a well-formed reply.
    """
    import html as _html
    import inspect as _inspect
    import re as _re
    import flask_app as F
    import journal as J
    import users as U

    src_of_dispatch = _inspect.getsource(F.handle_message)
    UID = 90001
    real_send, real_allowed, real_tg = F.send, F.allowed, F.tg
    captured: list[str] = []
    F.send = lambda cid, txt, **k: captured.append(txt)
    F.allowed = lambda uid: True

    def _tg(method, **payload):
        # /start posts its language picker through tg() directly, because it
        # needs an inline keyboard. Capturing only send() would report it as
        # silent and hide whether it answered at all.
        if method == "sendMessage" and payload.get("text"):
            captured.append(payload["text"])
        return {"ok": True, "result": {"message_id": 1}}
    F.tg = _tg

    # Commands that need the network (a quote, a candle history, an API key)
    # are out of scope here; this checks wiring and rendering, not data.
    NETWORKED = {"/signal", "/scan", "/backtest", "/calibration"}
    cmds = sorted({m.group(1) for m in
                   _re.finditer(r'cmd (?:==|in) \(?"(/[a-z]+)"', src_of_dispatch)}
                  - NETWORKED)
    extra = ["/start", "/help", "/menu", "/language", "/language bahasa",
             "/language english", "/setconf 70", "/setconf off",
             "/strategy", "/strategy shogun", "/strategy auto",
             "/daily", "/weekly", "/monthly", "/update", "/swingupdate",
             "/management", "/resetdata", "/news", "/subscription",
             "/usdrate", "/usdrate 16800", "/usdrate off",
             "/subs", "/grant", "/grant 123 30", "/revoke", "/revoke 123",
             "/notacommand"]

    # Every command runs twice, once with risk management off and once with
    # it on. Half of /status only exists in the second state, so a fixture
    # that never enables management silently skips the code it is meant to
    # cover — a renamed helper in that branch passed this check until the
    # second profile was added.
    PROFILES = {
        "management off": dict(
            language="en", strategy="crimson", min_confidence=0,
            management=None, day_trades=0, day_pl_usd=0.0,
            day_peak_usd=0.0, day_trough_usd=0.0),
        "management on": dict(
            language="id", strategy="auto", min_confidence=70,
            management={"enabled": True, "balance_usd": 5240.0,
                        "start_balance_usd": 5000.0, "risk_pct": 1.0,
                        "daily_dd_pct": 3.0, "profit_target_pct": 5.0,
                        "max_daily_trades": 6},
            day_trades=5, day_pl_usd=240.0,
            day_peak_usd=300.0, day_trough_usd=-140.0),
    }

    ok, failures = True, []
    for profile, settings in PROFILES.items():
        U.update(UID, **settings)
        for text in [c for c in cmds] + extra:
            captured.clear()
            try:
                F.handle_message({"chat": {"id": 1}, "from": {"id": UID},
                                  "text": text})
            except Exception as exc:                   # noqa: BLE001
                failures.append(f"[{profile}] {text} raised "
                                f"{type(exc).__name__}: {exc}")
                ok = False
                continue
            if not captured:
                failures.append(f"[{profile}] {text} produced no reply")
                ok = False
                continue
            ok &= _inspect_bodies(captured, f"[{profile}] {text}", failures)

    # Every user-facing command must actually change when the language does.
    # Comparing the two renders for inequality is not enough: one translated
    # word makes a mostly-English screen pass. So the Indonesian render is
    # searched for English function words instead. Trading vocabulary is
    # deliberately left in English throughout this bot ("entry", "lot",
    # "breakeven"), but "the", "your" and "closed" are prose, and prose in
    # the Indonesian render means a hardcoded string.
    #
    # Two- and three-letter words are deliberately absent from the list.
    # "in" looks like a certain tell until "All-in-One" splits into three
    # words and every screen naming the strategy fails.
    ENGLISH_TELLS = {
        "the", "and", "for", "with", "your", "you", "this", "that", "from",
        "have", "has", "been", "will", "are", "was", "were", "they", "their",
        "what", "when", "which", "would", "could", "should", "about",
        "there", "than", "then", "over", "under", "after", "before",
        "every", "each", "only", "just", "still", "does", "cannot", "left",
        "closed", "today", "yet", "most", "few", "days", "day", "into",
    }
    BILINGUAL = ["/status", "/history", "/setconf 70", "/symbols", "/help",
                 "/news", "/settings", "/management", "/resetdata",
                 "/strategy", "/daily", "/subscription", "/update"]
    U.update(UID, **PROFILES["management on"])
    for text in BILINGUAL:
        renders = {}
        for lang in ("en", "id"):
            U.update(UID, language=lang)
            captured.clear()
            F.handle_message({"chat": {"id": 1}, "from": {"id": UID},
                              "text": text})
            renders[lang] = captured[0] if captured else ""
        if renders["en"] == renders["id"]:
            failures.append(f"{text} renders identically in both languages "
                            f"— text is probably hardcoded English")
            ok = False
            continue
        # Command syntax inside <code> is legitimately English — the user
        # types "/management on", not a translation of it — so strip those
        # spans before looking for prose.
        prose = _re.sub(r"<code>.*?</code>", " ", renders["id"], flags=_re.S)
        words = set(_re.findall(r"[a-z]+", _html.unescape(prose).lower()))
        leaked = sorted(words & ENGLISH_TELLS)
        if leaked:
            failures.append(f"{text} leaks English into the Indonesian "
                            f"render: {', '.join(leaked[:6])}")
            ok = False

    U.update(UID, **PROFILES["management off"])

    # An unknown command must still answer, and known ones must not fall
    # through to that same answer.
    captured.clear()
    F.handle_message({"chat": {"id": 1}, "from": {"id": UID},
                      "text": "/notacommand"})
    unknown = captured[0] if captured else ""
    for text in ["/status", "/symbols", "/history", "/setconf 70", "/update"]:
        captured.clear()
        F.handle_message({"chat": {"id": 1}, "from": {"id": UID}, "text": text})
        if captured and captured[0] == unknown:
            failures.append(f"{text} fell through to the unknown-command reply")
            ok = False

    F.send, F.allowed, F.tg = real_send, real_allowed, real_tg
    J.wipe(UID)
    U.update(UID, management=None)

    if ok:
        print(f"  commands: {(len(cmds) + len(extra)) * 2} dispatched "
              f"across 2 account states, {len(BILINGUAL)} checked for "
              f"English leaking into Indonesian, all render clean")
    else:
        for f in failures[:8]:
            print(f"  x {f}")
    return ok



def check_calibration() -> bool:
    """The calibration lookup must prefer the most specific measured table,
    fall back cleanly, and never break monotonicity.

    Written after finding that the middle rung of a three-target ladder was
    never calibrated at all: TP1 and TP3 were measured while TP2 fell through
    to the model, which made the least reliable number on the signal the one
    sitting between two reliable ones.
    """
    cal = {
        "generated": "2026-01-01T00:00:00+00:00",
        "ladder": [1.0, 2.0, 3.0],
        "modes": {"intraday": {
            "trades": 900, "tp1": 0.50, "tp2": 0.30, "final": 0.20,
            "buckets": {"70-79": {"n": 300, "tp1": 0.55, "tp2": 0.34,
                                  "final": 0.22}}}},
        "strategies": {"shogun": {"intraday": {
            "trades": 400, "tp1": 0.62, "tp2": 0.41, "final": 0.28,
            "buckets": {"70-79": {"n": 150, "tp1": 0.66, "tp2": 0.45,
                                  "final": 0.31}}}}},
    }
    ok = True

    def run(strategy):
        return prob.estimate(score=75, targets_r=[1.0, 2.0, 3.0], room_rr=3.5,
                             mode="intraday", cal=cal, strategy=strategy)

    # 1. every rung is measured, including the middle one
    pooled = run(None)
    for i, row in enumerate(pooled["targets"]):
        if row["source"] == "model":
            print(f"  x rung {row['r']}R fell through to the model")
            ok = False

    # 2. a strategy with its own table uses it, not the pooled one
    own = run("shogun")
    if not all(t["source"] == "strategy-calibrated" for t in own["targets"]):
        print("  x shogun did not use its own table")
        ok = False
    if own["targets"][0]["p"] <= pooled["targets"][0]["p"]:
        print("  x shogun's higher measured TP1 did not raise its probability")
        ok = False

    # 3. a strategy with no table of its own falls back rather than failing
    back = run("kage")
    if [t["p"] for t in back["targets"]] != [t["p"] for t in pooled["targets"]]:
        print("  x fallback to the pooled table did not match the pooled result")
        ok = False

    # 4. monotone: a ladder cannot reach 3R without passing 2R
    for label, res in (("pooled", pooled), ("shogun", own), ("kage", back)):
        ps = [t["p"] for t in res["targets"]]
        if any(b > a + 1e-9 for a, b in zip(ps, ps[1:])):
            print(f"  x {label} probabilities are not non-increasing: {ps}")
            ok = False

    # 5. a thin bucket must be ignored, not trusted
    thin = {"modes": {"intraday": {"trades": 0, "buckets": {
        "70-79": {"n": 2, "tp1": 0.99, "tp2": 0.99, "final": 0.99}}}}}
    r = prob.estimate(score=75, targets_r=[1.0, 2.0, 3.0], room_rr=3.5,
                      mode="intraday", cal=thin)
    if any(t["source"] != "model" for t in r["targets"]):
        print("  x a 2-trade bucket was used as if it were evidence")
        ok = False

    # 6. an old mode-only file must not outrank the shipped measurement.
    #    /backtest --calibrate used to write one rate per mode with no TP2,
    #    so a single thin run silently beat 4,939 measured trades and left
    #    TP2 on the model while TP1 and TP3 were calibrated — a signal
    #    quoting 41% for two-R and 40% for three-R.
    import json as _json
    import os as _os
    import tempfile as _tf
    stale = {"modes": {"intraday": {"trades": 14, "tp1": 0.64, "final": 0.43,
             "buckets": {"90-100": {"n": 9, "tp1": 0.67, "final": 0.44}}}}}
    sp = _os.path.join(_tf.mkdtemp(), "calibration.json")
    _json.dump(stale, open(sp, "w"))
    real_file, real_cache = C.CALIBRATION_FILE, prob._cal_cache
    C.CALIBRATION_FILE = sp
    prob._cal_cache = None
    try:
        res = prob.estimate(score=92, targets_r=[1.0, 2.0, 3.0], room_rr=3.5,
                            mode="intraday", strategy="crimson")
        ps = [t["p"] for t in res["targets"]]
        if any(t["source"] == "model" for t in res["targets"]):
            print("  x a stale mode-only calibration left a rung on the model")
            ok = False
        if ps[1] - ps[2] < 0.03:
            print(f"  x TP2 {ps[1]:.0%} and TP3 {ps[2]:.0%} are indistinguishable "
                  f"— the stale file is still being used")
            ok = False
        # ...but a local file in the CURRENT format must still win.
        fresh = dict(cal)
        fresh["source"] = "local"
        _json.dump(fresh, open(sp, "w"))
        prob._cal_cache = None
        if prob.calibration_status().get("source_note") != "local":
            print("  x a fresh local calibration was ignored")
            ok = False
    finally:
        C.CALIBRATION_FILE, prob._cal_cache = real_file, real_cache

    # 7. a missing or malformed file leaves the model untouched
    for junk in ({}, {"modes": None}, {"modes": {"intraday": "nonsense"}}):
        r = prob.estimate(score=75, targets_r=[1.0, 2.0], room_rr=2.5,
                          mode="intraday", cal=junk)
        if any(t["source"] != "model" for t in r["targets"]):
            print(f"  x malformed calibration {junk} was trusted")
            ok = False

    if ok:
        print("  calibration: every rung measured, strategy table preferred, "
              "thin buckets ignored")
    return ok



def check_news() -> bool:
    """Event risk is computed from calendar rules, so the rules must hold.

    The blackout window is the part worth testing hardest: an earlier version
    read "clear" from the moment a release landed onwards, because the helper
    it used rolls forward to the next month as soon as the current one is in
    the past. It reported safety exactly during the half hour it existed to
    warn about.
    """
    import calendar as _cal
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    import news

    ok = True

    # 1. the first-Friday rule, at 08:30 New York, for three years
    for y in (2026, 2027, 2028):
        for m in range(1, 13):
            ny = news._first_friday(y, m).astimezone(news.NY)
            want = min(d for d in range(1, 8) if _cal.weekday(y, m, d) == 4)
            if (ny.day, ny.hour, ny.minute) != (want, 8, 30):
                print(f"  x NFP {y}-{m:02d} computed as {ny}")
                ok = False

    # 2. New York time, not a frozen UTC offset — the US moves its clocks
    if news._first_friday(2027, 1).hour == news._first_friday(2027, 7).hour:
        print("  x NFP ignores US daylight saving")
        ok = False

    # 3. the blackout covers the whole window, including during and after
    mid = news._first_friday(2026, 9)
    for off, want in ((-60, False), (-30, True), (0, True),
                      (20, True), (30, True), (31, False)):
        got = news.blackout(mid + _td(minutes=off)) is not None
        if got != want:
            print(f"  x blackout at {off:+d} min: got {got}, wanted {want}")
            ok = False

    # 4. a host with no timezone database must lose the calendar's accuracy,
    #    not the bot: news.py is imported by flask_app, so an exception here
    #    would take every command down for the sake of one feature
    if not isinstance(news.TZ_EXACT, bool):
        print("  x news does not report whether its timezone is exact")
        ok = False
    if news.TZ_EXACT and news._first_friday(2027, 7).hour == \
            news._first_friday(2027, 1).hour:
        print("  x an exact timezone still ignored daylight saving")
        ok = False

    # 5. upcoming is ordered and inside the horizon
    now = _dt.now(_tz.utc)
    up = news.upcoming(now, days=14)
    if up != sorted(up, key=lambda e: e.when_utc):
        print("  x upcoming events are not in time order")
        ok = False
    if any(e.when_utc < now or e.when_utc > now + _td(days=14) for e in up):
        print("  x upcoming returned an event outside the horizon")
        ok = False

    # 6. a broken events.json must be ignored, never raised
    import tempfile, os as _os
    real = news.EVENTS_FILE
    for junk in ("not json at all", "{}", '[{"utc": "nonsense"}]', "[1,2,3]"):
        p = _os.path.join(tempfile.mkdtemp(), "events.json")
        open(p, "w").write(junk)
        news.EVENTS_FILE = p
        try:
            news.upcoming(now)
        except Exception as exc:                        # noqa: BLE001
            print(f"  x malformed events.json raised {type(exc).__name__}")
            ok = False
    # 7. a well-formed user event is picked up
    p = _os.path.join(tempfile.mkdtemp(), "events.json")
    soon = (now + _td(days=2)).isoformat()
    open(p, "w").write(f'[{{"name":"CPI","utc":"{soon}","impact":"high"}}]')
    news.EVENTS_FILE = p
    if not any(e.name == "CPI" for e in news.upcoming(now)):
        print("  x a valid user event was not picked up")
        ok = False
    news.EVENTS_FILE = real

    if ok:
        print("  news: first-Friday rule holds, DST respected, blackout "
              "covers the release itself")
    return ok



def check_subscriptions() -> bool:
    """Access control, which is the one place a bug costs money or trust.

    The properties worth guaranteeing are that the operator cannot lock
    themselves out, that expiry is a fact about a date rather than about a
    job having run, and that renewing early does not destroy time already
    paid for.
    """
    import os as _os
    import tempfile as _tf
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    import config as _C
    import subscriptions as S

    real_store, real_owner = S.STORE, getattr(_C, "OWNER_IDS", set())
    S.STORE = _os.path.join(_tf.mkdtemp(), "subs.json")
    _C.OWNER_IDS = {111}
    ok = True
    now = _dt.now(_tz.utc)

    # 1. the owner is never a customer
    if not S.active(111) or S.days_left(111) != float("inf"):
        print("  x the owner is not unconditionally allowed")
        ok = False
    # ... and stays in even with the store deleted underneath them
    _os.unlink(S.STORE) if _os.path.exists(S.STORE) else None
    if not S.active(111):
        print("  x losing the store locked the owner out")
        ok = False

    # 2. a stranger is out until granted
    if S.active(222):
        print("  x an unknown user was allowed in")
        ok = False
    S.grant(222, 30, granted_by=111)
    if not S.active(222):
        print("  x a granted user was not allowed in")
        ok = False

    # 3. renewing early adds time rather than replacing it
    S.grant(222, 30, granted_by=111)
    if not 59 < S.days_left(222) <= 60:
        print(f"  x early renewal gave {S.days_left(222):.1f} days, wanted ~60")
        ok = False

    # 4. expiry is evaluated against the clock, not swept by a job
    if S.active(222, now=now + _td(days=61)):
        print("  x a lapsed subscription still counted as active")
        ok = False
    if not S.active(222, now=now + _td(days=59)):
        print("  x a live subscription was treated as lapsed")
        ok = False

    # 5. revoke is immediate and idempotent
    if not S.revoke(222) or S.active(222) or S.revoke(222):
        print("  x revoke did not behave as expected")
        ok = False

    # 6. a corrupt store denies strangers rather than admitting them
    for junk in ("not json", "[]", '{"222": {"until": "nonsense"}}'):
        open(S.STORE, "w").write(junk)
        if S.active(222):
            print(f"  x a corrupt store ({junk[:20]}) let a stranger in")
            ok = False
        if not S.active(111):
            print("  x a corrupt store locked the owner out")
            ok = False

    # 7. the gate must not change behaviour until it is switched on
    import flask_app as F
    was = _C.SUBSCRIPTIONS_ENABLED
    _C.SUBSCRIPTIONS_ENABLED = False
    _C.ALLOWED_USER_IDS = {999}
    if not F.allowed(999) or F.allowed(222):
        print("  x the legacy allowlist stopped working with subscriptions off")
        ok = False
    _C.SUBSCRIPTIONS_ENABLED = was

    S.STORE, _C.OWNER_IDS = real_store, real_owner
    if ok:
        print("  subscriptions: owner never expires, renewal extends, "
              "a corrupt store denies rather than admits")
    return ok



def check_management_lifecycle() -> bool:
    """Turning risk management off, or hitting the target, must leave nothing
    behind.

    A parked envelope is the dangerous state: the next trade would be sized
    from a balance the user last confirmed weeks ago, and the drawdown meter
    would be measured against a peak from a different day.
    """
    import flask_app as F
    import users as U

    UID = 90002
    ok = True
    env = {"enabled": True, "balance_usd": 5000.0,
           "start_balance_usd": 5000.0, "risk_pct": 1.0, "daily_dd_pct": 3.0,
           "profit_target_pct": 5.0, "max_daily_trades": 6}

    def dirty():
        U.update(UID, management=dict(env), day_trades=4, day_pl_usd=-30.0,
                 day_peak_usd=90.0, day_trough_usd=-210.0)

    def clean(label):
        nonlocal ok
        u = U.get(UID)
        if u.get("management") is not None:
            print(f"  x {label} left the envelope in place")
            ok = False
        for field in ("day_trades", "day_pl_usd", "day_peak_usd",
                      "day_trough_usd"):
            if u.get(field):
                print(f"  x {label} left {field}={u[field]!r} behind")
                ok = False

    # 1. explicit off deletes rather than disables
    dirty()
    U.management_off(UID)
    clean("/management off")

    # 2. reaching the target deletes it too, and says so once
    dirty()
    if F.check_profit_target(UID) is not None:
        print("  x the target fired while the account was still below it")
        ok = False
    m = dict(env)
    m["balance_usd"] = 5300.0            # +6%, past the 5% target
    U.update(UID, management=m)
    if not F.check_profit_target(UID):
        print("  x reaching the target produced no message")
        ok = False
    clean("hitting the profit target")
    if F.check_profit_target(UID) is not None:
        print("  x the target fired a second time after being cleared")
        ok = False

    # 3. a fresh envelope starts from zero, not from the old day's worst
    U.update(UID, management=dict(env))
    if U.max_drawdown_today(U.get(UID)) != 0.0:
        print("  x a new envelope inherited the previous day's drawdown")
        ok = False

    U.update(UID, management=None)
    if ok:
        print("  management: off and target-hit both delete the envelope, "
              "counters included")
    return ok



def check_cost_veto() -> bool:
    """No ruleset may issue a trade whose spread eats more than MAX_COST_R of
    its own risk.

    The recorded backtests contain trades paying 2.14 R in spread alone to
    open — a 214% edge just to break even. They come from a frozen tape,
    where ATR collapses and the stop shrinks with it. The existing dead-tape
    veto misses them because it is relative: over a long freeze the rolling
    median ATR falls too, so the ratio reads normal.

    Every ruleset is checked, because Ronin has its own evaluate() rather
    than calling the shared one, and a guard added in only one place left
    the default strategy uncovered.
    """
    import instruments as I
    import strategies as S
    from indicators import resample_ohlc

    base = synth(kind="uptrend", n=6000)
    base = base / base["close"].iloc[0] * 4500.0
    spec = C.MODES["intraday"]
    e = base
    t = resample_ohlc(base, "1h")
    b = resample_ohlc(base, "4h")
    inst = I.BY_KEY["xauusd"]

    real = I.Instrument.cost_r
    ok, checked = True, 0
    try:
        for key in S.ORDER:
            if key == "auto":
                continue
            for forced, should_veto in ((C.MAX_COST_R * 0.5, False),
                                        (C.MAX_COST_R * 1.5, True)):
                I.Instrument.cost_r = lambda self, d, _f=forced: _f
                plans = vetoed = 0
                for i in range(400, len(e), 97):
                    now = e.index[i]
                    r = S.REGISTRY[key].evaluate(
                        e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                        spec, now.to_pydatetime(), instrument=inst)
                    if not r.get("levels"):
                        continue
                    plans += 1
                    hit = any("Spread alone" in v for v in (r.get("vetoes") or []))
                    if hit:
                        vetoed += 1
                        if r["decision"] != "NO TRADE":
                            print(f"  x {key}: cost veto raised but decision "
                                  f"stayed {r['decision']}")
                            ok = False
                if not plans:
                    continue
                checked += plans
                if should_veto and vetoed != plans:
                    print(f"  x {key}: only {vetoed}/{plans} plans refused at "
                          f"cost {forced:.0%}, above the {C.MAX_COST_R:.0%} limit")
                    ok = False
                if not should_veto and vetoed:
                    print(f"  x {key}: {vetoed} plans refused at cost "
                          f"{forced:.0%}, below the limit")
                    ok = False
    finally:
        I.Instrument.cost_r = real

    if ok:
        print(f"  cost veto: {checked} plans across every ruleset, refused "
              f"above {C.MAX_COST_R:.0%} of risk and allowed below")
    return ok



def check_kage_contract() -> bool:
    """Kage's edge lives in three things the live machinery could quietly
    undo, so each is asserted rather than assumed.

    The stop must be 3 ATR. Cost in R is spread over stop distance, and
    scalp's own band caps stops at 1.6 ATR, which leaves the same gross edge
    netting +0.053 instead of +0.115. The clock must be honoured, because
    the edge is flat from 12 bars to 36 and gone by 6. And it must refuse to
    trade outside London and New York, where a retail gold spread is several
    times its daytime figure and where this rule would otherwise hand back a
    third of its profit.
    """
    import contextlib
    import io
    import instruments as I
    import strategies as S
    from indicators import resample_ohlc

    base = synth(kind="uptrend", n=4000)
    base = base / base["close"].iloc[0] * 4500.0
    spec = C.MODES["scalp"]
    ok = True

    def run_at(ts, force_break=False):
        e = base.copy()
        # Re-stamp the history so the last bar lands on the wanted timestamp,
        # keeping the 5-minute spacing the mode expects.
        e.index = pd.date_range(end=ts, periods=len(e), freq="5min", tz="UTC")
        if force_break:
            # Drive the final bar well clear of yesterday's high so the rule
            # certainly triggers. Without this the fixture may simply never
            # break a level, and every assertion below would be skipped
            # rather than checked — which is exactly how an earlier version
            # of this test passed while the stop band was clamping.
            yday = e.index.normalize()[-1] - pd.Timedelta(days=1)
            prior = e[e.index.normalize() == yday]
            top = float(prior["high"].max()) if len(prior) else float(e["high"].max())
            lift = top + 40.0
            e.iloc[-1, e.columns.get_loc("low")] = top - 1.0
            e.iloc[-1, e.columns.get_loc("close")] = lift
            e.iloc[-1, e.columns.get_loc("high")] = lift + 1.0
        t = resample_ohlc(e, "15min")
        b = resample_ohlc(e, "1h")
        with contextlib.redirect_stdout(io.StringIO()):
            return S.REGISTRY["kage"].evaluate(
                e, t, b, spec, ts.to_pydatetime(), instrument=I.BY_KEY["xauusd"])

    # A Wednesday inside the window, and the same bar outside it.
    inside = pd.Timestamp("2026-08-19 13:00", tz="UTC")
    outside = pd.Timestamp("2026-08-19 22:00", tz="UTC")
    weekend = pd.Timestamp("2026-08-22 13:00", tz="UTC")     # Saturday

    r_out = run_at(outside, force_break=True)
    if r_out.get("decision") != "NO TRADE":
        print("  x traded at 22:00 UTC, outside the thin-spread window")
        ok = False
    if not any("outside" in v for v in (r_out.get("vetoes") or [])):
        print("  x no veto explaining the out-of-hours refusal")
        ok = False

    r_we = run_at(weekend, force_break=True)
    if r_we.get("decision") != "NO TRADE":
        print("  x traded on a Saturday")
        ok = False

    r_in = run_at(inside, force_break=True)
    lv = r_in.get("levels")
    if not lv:
        print("  x the forced break produced no levels — the fixture no longer "
              "exercises the rule, so nothing below is actually being tested")
        ok = False
    else:
        want = C.KAGE_STOP_ATR
        if abs((lv.get("stop_atr") or 0) - want) > 0.05:
            print(f"  x stop is {lv.get('stop_atr')}x ATR, not the {want}x the "
                  f"rule was measured with (the mode band clamped it)")
            ok = False
        if r_in.get("time_exit_bars") != C.KAGE_HOLD:
            print(f"  x clock is {r_in.get('time_exit_bars')}, not {C.KAGE_HOLD}")
            ok = False

    # Scalp needs 48h of history for a complete prior day.
    if C.MODES["scalp"].bars < 576:
        print(f"  x scalp requests {C.MODES['scalp'].bars} bars — under 576 the "
              f"prior day arrives truncated and PDH/PDL are wrong")
        ok = False

    if ok:
        print(f"  kage: {C.KAGE_STOP_ATR}x ATR stop survives the mode band, "
              f"{C.KAGE_HOLD}-bar clock set, refuses nights and weekends")
    return ok



def check_onboarding() -> bool:
    """A stranger's first message is /start, and at that point they have no
    subscription. If /start sits behind the access gate they get "not
    authorised" and no way to ask for access, so the gate placement is the
    thing worth testing, not the wording.
    """
    import flask_app as F
    import i18n as _i
    import users as U

    UID = 90003
    ok = True
    real_send, real_tg, real_allowed = F.send, F.tg, F.allowed
    sent, calls = [], []
    F.send = lambda c, t, **k: sent.append(t)
    F.tg = lambda m, **p: (calls.append((m, p)) or {"ok": True,
                                                    "result": {"message_id": 1}})
    F.allowed = lambda uid: False              # a stranger

    try:
        F.handle_message({"chat": {"id": 1}, "from": {"id": UID},
                          "text": "/start"})
        picker = [p for m, p in calls if m == "sendMessage"]
        if not picker:
            print("  x /start sent nothing — it is behind the access gate")
            return False
        kb = (picker[-1].get("reply_markup") or {}).get("inline_keyboard") or []
        datas = [b.get("callback_data") for row in kb for b in row]
        if sorted(datas) != ["lang|en", "lang|id"]:
            print(f"  x /start offered {datas}, not both languages")
            ok = False

        for data, want in (("lang|en", _i.EN), ("lang|id", _i.ID)):
            sent.clear()
            F.handle_callback({"id": "x", "from": {"id": UID}, "data": data,
                               "message": {"chat": {"id": 1}, "message_id": 2}})
            if U.get(UID).get("language") != want:
                print(f"  x picking {data} did not set the language")
                ok = False
            if not sent:
                print(f"  x picking {data} sent no welcome")
                ok = False
                continue
            body = sent[0]
            if str(UID) not in body:
                print("  x the locked welcome omits the user's own ID, which "
                      "is the one thing they need to ask for access")
                ok = False
            if _i.t("welcome", want).split("\n")[0][:12] not in body:
                print(f"  x welcome was not in the chosen language ({want})")
                ok = False

        # With access, the closing block must change rather than still telling
        # a paying subscriber to go and subscribe.
        F.allowed = lambda uid: True
        sent.clear()
        F.handle_callback({"id": "x", "from": {"id": UID}, "data": "lang|en",
                           "message": {"chat": {"id": 1}, "message_id": 2}})
        if sent and "Subscription required" in sent[0]:
            print("  x a user WITH access is still told to subscribe")
            ok = False
    finally:
        F.send, F.tg, F.allowed = real_send, real_tg, real_allowed

    if ok:
        print("  onboarding: /start works without access, both languages, "
              "welcome carries the user's ID")
    return ok



def check_usdrate() -> bool:
    """A rate the owner types sizes every subscriber's IDR trade, so the two
    things that matter are that only the owner can set it and that setting it
    takes effect immediately rather than after the hour-long cache expires.
    """
    import os as _os
    import tempfile as _tf
    import config as _C
    import flask_app as F
    import money
    import subscriptions as S
    import users as U

    real_store, real_owner, real_over, real_cfg = (
        S.STORE, getattr(_C, "OWNER_IDS", set()), money.OVERRIDE_FILE,
        getattr(_C, "USD_IDR_RATE", ""))
    real_send, real_enabled, real_free = F.send, _C.SUBSCRIPTIONS_ENABLED, money._free_fx
    sent = []
    F.send = lambda c, t, **k: sent.append(t)
    _C.SUBSCRIPTIONS_ENABLED = True
    _C.OWNER_IDS = {111}
    _C.USD_IDR_RATE = ""
    S.STORE = _os.path.join(_tf.mkdtemp(), "s.json")
    money.OVERRIDE_FILE = _os.path.join(_tf.mkdtemp(), "fx.json")
    money._free_fx = lambda c, timeout=4.0: None
    money._rate_cache.clear()
    ok = True

    def dead(*a, **k):
        raise RuntimeError("provider has no USD/IDR")

    try:
        S.grant(222, 30, granted_by=111)
        U.update(111, language="en")
        U.update(222, language="en")

        def run(uid, text):
            sent.clear()
            F.handle_message({"chat": {"id": 1}, "from": {"id": uid},
                              "text": text})
            return sent[0] if sent else ""

        # A paying subscriber must not be able to move everyone's sizing.
        run(222, "/usdrate 99000")
        if money._manual_rate("IDR") is not None:
            print("  x a non-owner set the exchange rate")
            ok = False

        run(111, "/usdrate 17700")
        if money._manual_rate("IDR") != 17700:
            print(f"  x owner set 17700, stored {money._manual_rate('IDR')}")
            ok = False

        got = money.to_usd(17_700, "IDR", fetch=dead)
        if got is None or abs(got - 1.0) > 0.01:
            print(f"  x 17,700 IDR should be about $1.00 at this rate, got {got}")
            ok = False

        # A LIVE rate is cached for an hour and is checked before any manual
        # one, so an owner correcting a bad live quote would be ignored until
        # the cache expired. Setting an override must evict it. Priming the
        # cache directly is the only way to reach this path, since the manual
        # rate itself is never cached.
        money._rate_cache["IDR"] = (__import__("time").time(), 1.0 / 99_000)
        run(111, "/usdrate 16800")
        got = money.to_usd(16_800, "IDR", fetch=dead)
        if got is None or abs(got - 1.0) > 0.01:
            print(f"  x a stale cached rate survived the correction: 16,800 "
                  f"IDR came out as {got}, not ~$1.00")
            ok = False

        # Indonesian thousands separator.
        run(111, "/usdrate 16.500")
        if money._manual_rate("IDR") != 16500:
            print(f"  x '16.500' parsed as {money._manual_rate('IDR')}")
            ok = False
        got = money.to_usd(16_500, "IDR", fetch=dead)
        if got is None or abs(got - 1.0) > 0.01:
            print(f"  x after correcting the rate a conversion still used the "
                  f"cached one: 16,500 IDR came out as {got}, not ~$1.00")
            ok = False

        for junk in ("banana", "5", "9999999", "-100"):
            before = money._manual_rate("IDR")
            run(111, f"/usdrate {junk}")
            if money._manual_rate("IDR") != before:
                print(f"  x '{junk}' was accepted as a rate")
                ok = False

        run(111, "/usdrate off")
        if money._manual_rate("IDR") is not None:
            print("  x clearing the rate left it set")
            ok = False
    finally:
        F.send, _C.SUBSCRIPTIONS_ENABLED, money._free_fx = (
            real_send, real_enabled, real_free)
        S.STORE, _C.OWNER_IDS = real_store, real_owner
        money.OVERRIDE_FILE, _C.USD_IDR_RATE = real_over, real_cfg
        money._rate_cache.clear()

    if ok:
        print("  usdrate: owner only, applies immediately, rejects nonsense")
    return ok



def check_vol_baseline() -> bool:
    """"Normal volatility" must be measured over bars where the market was
    open.

    A plain median of the last 100 bars reaches into the weekend on a Monday
    morning, where gold's bars are a quarter of a point against four points
    live. The median lands in the frozen cluster and an ordinary ATR reads as
    sixteen times normal, so the bot refuses the day as a news spike. On real
    history that was 27% of Monday 06:00-14:00 bars against 0.1% midweek.
    """
    import numpy as np
    import strategy as base

    ok = True
    # Two clusters, as a real Monday window has: ~40 frozen weekend bars
    # then a live session.
    # The frozen stretch has to fall INSIDE the trailing baseline window, and
    # outnumber the live bars in it, or a plain median never lands in the
    # dead cluster and the fixture proves nothing. That is a real Monday
    # 09:00: the weekend is still most of the last 100 bars.
    rng = np.random.default_rng(5)
    n_frozen, n_live = 70, 45
    frozen = np.full(n_frozen, 4500.0) + rng.normal(0, 0.02, n_frozen).cumsum()
    live = 4500.0 + rng.normal(0, 1.2, n_live).cumsum()
    close = np.concatenate([frozen, live])
    idx = pd.date_range("2026-08-24", periods=len(close), freq="15min", tz="UTC")
    span = np.concatenate([np.full(n_frozen, 0.05), np.full(n_live, 3.0)])
    df = pd.DataFrame({"open": close, "close": close,
                       "high": close + span, "low": close - span}, index=idx)

    e = base.snapshot(df)
    if not (0.5 <= e["atr_ratio"] <= 2.0):
        print(f"  x a normal live bar after a frozen weekend reads "
              f"{e['atr_ratio']:.1f}x normal — the baseline includes dead bars")
        ok = False
    if e["atr_ratio"] > C.VOL_SPIKE_MULT:
        print(f"  x it would be refused as a news spike "
              f"({e['atr_ratio']:.1f}x > {C.VOL_SPIKE_MULT})")
        ok = False

    # A genuine spike must still be caught — tested on an ALL-LIVE window,
    # which is the only situation where a baseline exists to spike against.
    # Inside a mostly-frozen window the ratio is deliberately silent; that
    # trade-off is documented in _live_atr_median and costs the Sunday
    # reopen hour, where the dealing-cost limit still applies.
    lidx = pd.date_range("2026-08-19", periods=200, freq="15min", tz="UTC")
    lc = 4500.0 + rng.normal(0, 1.2, 200).cumsum()
    ldf = pd.DataFrame({"open": lc, "close": lc, "high": lc + 3.0,
                        "low": lc - 3.0}, index=lidx)
    calm = base.snapshot(ldf)["atr_ratio"]
    if not (0.5 <= calm <= 2.0):
        print(f"  x a calm all-live window reads {calm:.2f}x")
        ok = False
    spiked = ldf.copy()
    spiked.iloc[-1, spiked.columns.get_loc("high")] = float(lc[-1]) + 120
    spiked.iloc[-1, spiked.columns.get_loc("low")] = float(lc[-1]) - 120
    sr = base.snapshot(spiked)["atr_ratio"]
    if sr <= C.VOL_SPIKE_MULT:
        print(f"  x a 40x-range bar in a live window reads only {sr:.2f}x — "
              f"a real news spike would be traded")
        ok = False

    # THE REOPEN: the entire window is still weekend and the first live bars
    # arrive. Filtering alone cannot help — with every bar frozen the "busy"
    # level is frozen too — so the baseline must be abandoned. Without this
    # the reopen refused 29% of bars at up to 147x.
    reopen_close = np.concatenate([
        np.full(95, 4500.0) + rng.normal(0, 0.02, 95).cumsum(),
        4500.0 + rng.normal(0, 1.2, 8).cumsum()])
    ridx = pd.date_range("2026-08-23 18:00", periods=len(reopen_close),
                         freq="15min", tz="UTC")
    rspan = np.concatenate([np.full(95, 0.05), np.full(8, 3.0)])
    rdf = pd.DataFrame({"open": reopen_close, "close": reopen_close,
                        "high": reopen_close + rspan,
                        "low": reopen_close - rspan}, index=ridx)
    rr = base.snapshot(rdf)["atr_ratio"]
    if rr > C.VOL_SPIKE_MULT:
        print(f"  x the first live bars after a weekend read {rr:.1f}x normal "
              f"— the reopen is still refused as a news spike")
        ok = False

    # THE MIXED WINDOW, tested on the real data rather than a fixture.
    # Monday morning a few hours in: under half the window is frozen, so the
    # dead-window guard stays quiet by design, and the live bars are still
    # ramping up from the reopen — which drags a plain median to the bottom
    # of the live cluster. Several synthetic attempts failed to reproduce
    # this while the real series does it every week, so the committed
    # candles are the fixture.
    import os as _os
    hist = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "data", "xauusd_15min.csv")
    if _os.path.exists(hist):
        real = pd.read_csv(hist, parse_dates=["datetime"]).set_index("datetime")
        if real.index.tz is None:
            real.index = real.index.tz_localize("UTC")
        dow, hour = real.index.dayofweek, real.index.hour
        # Every Monday in the file, not the first few — an early slice
        # happened to miss the ramp entirely and passed with the filter
        # removed.
        mon = np.where((dow == 0) & (hour >= 6) & (hour < 14))[0]
        mon = mon[mon >= 300]
        if len(mon) >= 60:
            ratios = np.array([base.snapshot(real.iloc[i - 300:i + 1])["atr_ratio"]
                               for i in mon[::max(1, len(mon) // 120)]])
            refused = float((ratios > C.VOL_SPIKE_MULT).mean())
            if refused > 0.02:
                print(f"  x {refused:.1%} of real Monday 06:00-14:00 bars are "
                      f"refused as a news spike (worst {ratios.max():.1f}x) — "
                      f"midweek is 0.3%")
                ok = False

    # ...and a frozen bar must still be far too thin to trade.
    frozen_only = df.iloc[:n_frozen]
    if len(frozen_only) >= 60:
        fr = base.snapshot(frozen_only)
        if 3.0 * fr["atr"] > 2.0:
            print(f"  x a frozen-tape bar produced a {3 * fr['atr']:.2f} pt stop")
            ok = False

    if ok:
        print("  volatility baseline: a frozen weekend no longer reads as a "
              "news spike, real spikes still do")
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
    ok &= check_resolve_robustness()
    ok &= check_webhook_retry()

    print("\nCommands:")
    ok &= check_commands()
    ok &= check_calibration()
    ok &= check_news()
    ok &= check_subscriptions()
    ok &= check_management_lifecycle()
    ok &= check_cost_veto()
    ok &= check_kage_contract()
    ok &= check_onboarding()
    ok &= check_usdrate()
    ok &= check_vol_baseline()
    raise SystemExit(0 if ok else 1)
