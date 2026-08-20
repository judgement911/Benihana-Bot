"""
THE TRADABLE UNIVERSE
=====================

CFDs only: FX, metals, energy, indices. No crypto — it trades through
weekends and holidays, its volatility regime is nothing like the rest, and
the session logic in config.MODES is built around London and New York.

Every instrument carries the three numbers that stop the bot lying to you
about a trade:

  contract_size  units per 1.00 lot. Gold is 100 oz, a forex lot is 100,000
                 base units, an index CFD is usually 1 per point. Sizing with
                 gold's 100 for every symbol overstates FX position size by a
                 factor of a thousand.
  digits         price decimals. EUR/USD printed to 2dp is 1.08 for entry,
                 stop and both targets — four identical numbers.
  pip            what "one unit of movement" means, so risk reads as 12 pips
                 on cable and 6.34 pts on gold, the way a trader says it.
  spread         typical retail dealing cost in price units. The probability
                 model divides it by the stop distance to get the cost in R,
                 so tightening a stop correctly makes the odds worse instead
                 of looking free.

Index and energy contract sizes vary by broker. The defaults here are the
common retail CFD conventions; check yours before trusting the lot size.

Provider coverage is NOT uniform. Twelve Data's free tier reliably serves FX
and metals; indices and energy usually need a paid plan. Each instrument
declares its tier so a scan can default to what will actually return data,
and `python check_universe.py` probes your key and tells you the truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FX, METAL, ENERGY, INDEX = "fx", "metal", "energy", "index"

CLASS_LABEL = {FX: "Forex", METAL: "Metals", ENERGY: "Energy", INDEX: "Indices"}

# Tier: "core" is what a free forex data plan serves. "extended" commonly
# needs a paid plan — still fully supported, just likely to return no data.
CORE, EXTENDED = "core", "extended"


@dataclass(frozen=True)
class Instrument:
    key: str                 # canonical lookup key: "eurusd"
    symbol: str              # provider symbol: "EUR/USD"
    name: str                # "Euro / Dollar"
    asset_class: str
    contract_size: float
    digits: int
    pip: float
    spread: float = 0.0      # typical retail spread, in price units
    tier: str = CORE
    aliases: tuple = field(default_factory=tuple)

    @property
    def display(self) -> str:
        return self.key.upper()

    @property
    def pip_label(self) -> str:
        return "pips" if self.asset_class == FX else "pts"

    def fmt(self, price: float) -> str:
        return f"{price:,.{self.digits}f}"

    def risk_units(self, price_distance: float) -> float:
        """Stop distance the way a trader says it: pips for FX, points else."""
        return abs(price_distance) / self.pip

    def cost_r(self, stop_distance: float) -> float | None:
        """Dealing cost as a fraction of the 1R stop. None when unknown."""
        if not self.spread or stop_distance <= 0:
            return None
        return self.spread / float(stop_distance)

    def fmt_risk(self, price_distance: float) -> str:
        u = self.risk_units(price_distance)
        return f"{u:,.1f} {self.pip_label}" if u < 100 else f"{u:,.0f} {self.pip_label}"

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0] if "/" in self.symbol else ""

    @property
    def quote(self) -> str:
        return self.symbol.split("/")[1] if "/" in self.symbol else "USD"

    def usd_per_quote(self, price: float) -> float | None:
        """How many USD one unit of the quote currency is worth.

        A move of one price unit pays out in the QUOTE currency, so sizing in
        dollars needs this factor. Two cases are exact and free:

          quote is USD   — EUR/USD, XAU/USD, US500. A dollar is a dollar.
          base is USD    — USD/JPY, USD/CHF. The price IS USD/quote, so one
                           unit of quote is 1/price dollars.

        A cross (EUR/JPY, GBP/AUD) pays in a currency the bot has no rate for
        without another request, so this returns None and the caller declines
        to print a lot size rather than printing a wrong one.
        """
        if self.quote == "USD":
            return 1.0
        if self.base == "USD":
            return (1.0 / price) if price else None
        return None


def _fx(key: str, name: str, *aliases: str) -> Instrument:
    jpy = key.endswith("jpy")
    pip = 0.01 if jpy else 0.0001
    # Majors quote around a pip; crosses are routinely two to three.
    major = key in ("eurusd", "gbpusd", "usdjpy", "usdchf",
                    "usdcad", "audusd", "nzdusd")
    return Instrument(
        key=key, symbol=f"{key[:3].upper()}/{key[3:].upper()}", name=name,
        asset_class=FX, contract_size=100_000.0,
        digits=3 if jpy else 5, pip=pip,
        spread=pip * (1.2 if major else 2.5),
        tier=CORE, aliases=aliases,
    )


_ALL: list[Instrument] = [
    # ---------------------------------------------------------------- metals
    Instrument("xauusd", "XAU/USD", "Gold", METAL, 100.0, 2, 1.0, 0.30, CORE,
               ("gold", "xau")),
    Instrument("xagusd", "XAG/USD", "Silver", METAL, 5_000.0, 3, 0.01, 0.02, CORE,
               ("silver", "xag")),
    Instrument("xptusd", "XPT/USD", "Platinum", METAL, 100.0, 2, 1.0, 1.20, EXTENDED,
               ("platinum", "xpt")),
    Instrument("xpdusd", "XPD/USD", "Palladium", METAL, 100.0, 2, 1.0, 2.00, EXTENDED,
               ("palladium", "xpd")),

    # ------------------------------------------------------------ fx majors
    _fx("eurusd", "Euro / Dollar", "euro", "fiber"),
    _fx("gbpusd", "Pound / Dollar", "cable", "pound", "sterling"),
    _fx("usdjpy", "Dollar / Yen", "yen"),
    _fx("usdchf", "Dollar / Franc", "swissy", "franc"),
    _fx("usdcad", "Dollar / Loonie", "loonie", "cad"),
    _fx("audusd", "Aussie / Dollar", "aussie", "aud"),
    _fx("nzdusd", "Kiwi / Dollar", "kiwi", "nzd"),

    # ------------------------------------------------------------ fx crosses
    _fx("eurjpy", "Euro / Yen"),
    _fx("gbpjpy", "Pound / Yen", "guppy", "beast"),
    _fx("eurgbp", "Euro / Pound"),
    _fx("euraud", "Euro / Aussie"),
    _fx("eurchf", "Euro / Franc"),
    _fx("eurcad", "Euro / Loonie"),
    _fx("eurnzd", "Euro / Kiwi"),
    _fx("gbpaud", "Pound / Aussie"),
    _fx("gbpcad", "Pound / Loonie"),
    _fx("gbpchf", "Pound / Franc"),
    _fx("gbpnzd", "Pound / Kiwi"),
    _fx("audjpy", "Aussie / Yen"),
    _fx("audnzd", "Aussie / Kiwi"),
    _fx("audcad", "Aussie / Loonie"),
    _fx("audchf", "Aussie / Franc"),
    _fx("cadjpy", "Loonie / Yen"),
    _fx("cadchf", "Loonie / Franc"),
    _fx("chfjpy", "Franc / Yen"),
    _fx("nzdjpy", "Kiwi / Yen"),
    _fx("nzdcad", "Kiwi / Loonie"),
    _fx("nzdchf", "Kiwi / Franc"),

    # ---------------------------------------------------------------- energy
    Instrument("usoil", "WTI/USD", "WTI Crude", ENERGY, 1_000.0, 2, 0.01,
               0.03, EXTENDED, ("wti", "oil", "crude", "usoil", "wtiusd")),
    Instrument("ukoil", "BRENT/USD", "Brent Crude", ENERGY, 1_000.0, 2, 0.01,
               0.03, EXTENDED, ("brent", "ukoil", "brentusd")),
    Instrument("ngas", "NG/USD", "Natural Gas", ENERGY, 10_000.0, 3, 0.001,
               0.005, EXTENDED, ("natgas", "gas", "naturalgas")),

    # --------------------------------------------------------------- indices
    Instrument("us30", "DJI", "Dow Jones 30", INDEX, 1.0, 1, 1.0, 2.0, EXTENDED,
               ("dow", "dji", "dowjones", "wallstreet")),
    Instrument("us500", "SPX", "S&P 500", INDEX, 1.0, 1, 1.0, 0.5, EXTENDED,
               ("sp500", "spx", "spx500", "sandp")),
    Instrument("ustec", "NDX", "Nasdaq 100", INDEX, 1.0, 1, 1.0, 1.5, EXTENDED,
               ("nas100", "nasdaq", "ndx", "nq", "us100")),
    Instrument("ger40", "DAX", "DAX 40", INDEX, 1.0, 1, 1.0, 1.0, EXTENDED,
               ("dax", "de40", "ger30", "germany40")),
    Instrument("uk100", "FTSE", "FTSE 100", INDEX, 1.0, 1, 1.0, 1.0, EXTENDED,
               ("ftse", "footsie", "uk")),
    Instrument("jp225", "N225", "Nikkei 225", INDEX, 1.0, 1, 1.0, 8.0, EXTENDED,
               ("nikkei", "n225", "japan225")),
    Instrument("fra40", "CAC", "CAC 40", INDEX, 1.0, 1, 1.0, 1.0, EXTENDED,
               ("cac", "cac40", "france40")),
    Instrument("aus200", "ASX", "ASX 200", INDEX, 1.0, 1, 1.0, 1.0, EXTENDED,
               ("asx", "asx200", "australia200")),
]

BY_KEY: dict[str, Instrument] = {i.key: i for i in _ALL}

# Every spelling that resolves to an instrument.
LOOKUP: dict[str, Instrument] = {}
for _i in _ALL:
    LOOKUP[_i.key] = _i
    LOOKUP[_i.symbol.replace("/", "").lower()] = _i
    for _a in _i.aliases:
        LOOKUP[_a] = _i


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def find(text: str) -> Instrument | None:
    """Resolve user input to an instrument. Returns None if unknown."""
    return LOOKUP.get(_norm(text))


def by_symbol(symbol: str) -> Instrument | None:
    """Reverse lookup from a provider symbol like 'XAU/USD'."""
    return LOOKUP.get(symbol.replace("/", "").lower())


def all_instruments(asset_class: str | None = None,
                    tier: str | None = None) -> list[Instrument]:
    out = _ALL
    if asset_class:
        out = [i for i in out if i.asset_class == asset_class]
    if tier:
        out = [i for i in out if i.tier == tier]
    return list(out)


def grouped() -> dict[str, list[Instrument]]:
    return {label: [i for i in _ALL if i.asset_class == cls]
            for cls, label in CLASS_LABEL.items()}


# Default gold, so every existing call site keeps behaving as it did.
GOLD = BY_KEY["xauusd"]
