"""
WHAT EACH STRATEGY HAS ACTUALLY DONE
====================================

A lookup over measured backtest results, so the bot can answer "has this
strategy ever made money in this mode on this instrument?" with a number
instead of an opinion.

This exists because the confidence scores turned out to be worthless for
choosing between strategies. Measured on gold, three of the five showed
LOWER expectancy in their highest confidence bucket than their middle one —
picking the most confident signal would have actively selected the worse
trades. A score nobody checked against outcomes is decoration.

The bar for calling something proven is a t-statistic above 1.96 on at least
a hundred trades. Everything below that is reported as unproven rather than
quietly ranked, because a positive expectancy that cannot be distinguished
from zero is not evidence of anything.

REGENERATE with:
    python3 research/fullgrid.py xauusd
    python3 -c "...see the commit that added this..."

The numbers are honest about what they are: whole-file runs with in-sample
and out-of-sample mixed together, which makes them the optimistic case.
"""
from __future__ import annotations

import json
import os

SIGNIFICANCE = 1.96
MIN_TRADES = 100

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measured.json")
_cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_PATH, "r", encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (OSError, ValueError):
            _cache = {"records": [], "caveat": "no measured data available"}
    return _cache


def caveat() -> str:
    return _load().get("caveat", "")


def records(instrument=None, mode=None, strategy=None) -> list[dict]:
    out = _load().get("records", [])
    if instrument:
        out = [r for r in out if r["instrument"] == instrument]
    if mode:
        out = [r for r in out if r["mode"] == mode]
    if strategy:
        out = [r for r in out if r["strategy"] == strategy]
    return out


def proven(instrument: str, mode: str = None) -> list[dict]:
    """Combinations that cleared the bar, best expectancy first."""
    ok = [r for r in records(instrument, mode)
          if r["t"] >= SIGNIFICANCE and r["expectancy"] > 0
          and r["trades"] >= MIN_TRADES]
    return sorted(ok, key=lambda r: -r["expectancy"])


def best_for(instrument: str, mode: str) -> dict | None:
    got = proven(instrument, mode)
    return got[0] if got else None


def score_of(instrument: str, mode: str, strategy: str) -> dict | None:
    got = records(instrument, mode, strategy)
    return max(got, key=lambda r: r["expectancy"]) if got else None


def where_it_works(instrument: str) -> dict[str, list[dict]]:
    """Every mode that has at least one proven combination."""
    out = {}
    for r in proven(instrument):
        out.setdefault(r["mode"], []).append(r)
    return out
