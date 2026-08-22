"""
ENGLISH AND BAHASA INDONESIA
============================

One table, two columns. `t(key, lang, **kw)` returns the string; an unknown
key returns the key itself so a missing translation is visible in testing
rather than silently blank in production.

Indonesian here is the register a trader actually uses — "entry", "stop
loss", "lot", "risk" are left in English because translating them ("hentikan
kerugian") reads as machine output, not as a person. What gets translated is
the prose around them.
"""
from __future__ import annotations

EN, ID = "en", "id"
LANGS = (EN, ID)

S: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- signal
    "wait": {EN: "WAIT", ID: "TUNGGU"},
    "setup": {EN: "setup", ID: "setup"},
    "no_trade": {EN: "NO TRADE", ID: "TIDAK TRADING"},
    "active": {EN: "ACTIVE", ID: "AKTIF"},
    "near": {EN: "NEAR", ID: "DEKAT"},
    "breakeven": {EN: "BREAKEVEN", ID: "BREAKEVEN"},
    "completed": {EN: "COMPLETED", ID: "SELESAI"},
    "stopped": {EN: "STOPPED", ID: "KENA STOP"},
    "expired": {EN: "EXPIRED", ID: "KEDALUWARSA"},
    "cancelled": {EN: "CANCELLED", ID: "DIBATALKAN"},
    "entry": {EN: "Entry", ID: "Entry"},
    "stop": {EN: "Stop", ID: "Stop"},
    "at_market": {EN: "at market", ID: "harga pasar"},
    "price_now": {EN: "price now", ID: "harga sekarang"},
    "risk": {EN: "risk", ID: "risiko"},
    "lots": {EN: "lots", ID: "lot"},
    "confidence": {EN: "Confidence", ID: "Keyakinan"},
    "odds": {EN: "Odds", ID: "Peluang"},
    "exp": {EN: "Exp", ID: "Ekspektasi"},
    "needs": {EN: "Needs", ID: "Menunggu"},
    "levels_move": {
        EN: "Levels move until it fills.",
        ID: "Level bergerak sampai order terisi.",
    },
    "not_live": {
        EN: "Not live — levels move until it triggers.",
        ID: "Belum aktif — level bergerak sampai terpicu.",
    },
    "news_hour": {
        EN: "High-impact US data often lands this hour.",
        ID: "Data ekonomi AS berdampak tinggi sering rilis jam ini.",
    },
    "lot_too_small": {
        EN: "Below the {min} lot minimum — this risk is too small to trade here.",
        ID: "Di bawah minimum {min} lot — risiko ini terlalu kecil untuk pair ini.",
    },
    "order_market": {
        EN: "Every condition met on the last close — executable now",
        ID: "Semua syarat terpenuhi pada candle terakhir — bisa dieksekusi sekarang",
    },
    "order_limit": {
        EN: "Rests in the EMA20 zone, {away} from here",
        ID: "Menunggu di zona EMA20, {away} dari harga sekarang",
    },
    "order_stop": {
        EN: "Already in the zone — fills only if the turn confirms",
        ID: "Sudah di zona — hanya terisi jika pembalikan terkonfirmasi",
    },
    "disclaimer": {
        EN: ("Signals only. Trade at your own risk. "
             "We are not responsible for any losses."),
        ID: ("Sinyal hanya untuk informasi. Trading dengan risiko Anda sendiri. "
             "Kami tidak bertanggung jawab atas kerugian Anda."),
    },

    # ------------------------------------------------------------- sessions
    "NY Session": {EN: "NY Session", ID: "Sesi New York"},
    "London Session": {EN: "London Session", ID: "Sesi London"},
    "Asia Session": {EN: "Asia Session", ID: "Sesi Asia"},
    "Sydney Session": {EN: "Sydney Session", ID: "Sesi Sydney"},
    "High Volatile": {EN: "High Volatile", ID: "Volatilitas Tinggi"},
    "Medium Volatile": {EN: "Medium Volatile", ID: "Volatilitas Sedang"},
    "Low Volatile": {EN: "Low Volatile", ID: "Volatilitas Rendah"},
    "market_closed": {EN: "Market closed", ID: "Pasar tutup"},

    # ------------------------------------------------------------- settings
    "lang_set": {
        EN: "Language set to English.",
        ID: "Bahasa diatur ke Bahasa Indonesia.",
    },
    "lang_usage": {
        EN: "Usage: <code>/language english</code> or <code>/language bahasa</code>",
        ID: "Cara pakai: <code>/language english</code> atau <code>/language bahasa</code>",
    },
    "conf_set": {
        EN: "Only signals with confidence {n}% or higher will be sent.",
        ID: "Hanya sinyal dengan keyakinan {n}% ke atas yang akan dikirim.",
    },
    "conf_cleared": {
        EN: "Confidence filter removed — every signal will be sent.",
        ID: "Filter keyakinan dihapus — semua sinyal akan dikirim.",
    },
    "conf_usage": {
        EN: "Usage: <code>/setconf 80</code> (0–99, or <code>off</code>)",
        ID: "Cara pakai: <code>/setconf 80</code> (0–99, atau <code>off</code>)",
    },
    "conf_below": {
        EN: "Confidence {got}% is below your {want}% filter. Not sent.",
        ID: "Keyakinan {got}% di bawah filter {want}% Anda. Tidak dikirim.",
    },

    # ------------------------------------------------------------ money/risk
    "risk_unreadable": {
        EN: ("Could not read the risk amount {raw}. Try <code>20$</code>, "
             "<code>100 USD</code> or <code>300k IDR</code>."),
        ID: ("Tidak bisa membaca jumlah risiko {raw}. Coba <code>20$</code>, "
             "<code>100 USD</code> atau <code>300k IDR</code>."),
    },
    "fx_unavailable": {
        EN: ("Cannot convert {ccy} right now — the exchange rate is "
             "unavailable, and guessing it would mis-size your position."),
        ID: ("Tidak bisa mengonversi {ccy} sekarang — kurs tidak tersedia, "
             "dan menebaknya akan membuat ukuran posisi salah."),
    },

    # --------------------------------------------------------------- errors
    "unknown_symbol": {
        EN: "Don't know <b>{what}</b>. Try /symbols for the list.",
        ID: "Tidak mengenal <b>{what}</b>. Lihat /symbols untuk daftarnya.",
    },
    "data_problem": {EN: "Data problem", ID: "Masalah data"},
    "unexpected": {EN: "Unexpected error", ID: "Kesalahan tak terduga"},
    "not_authorised": {EN: "Not authorised.", ID: "Tidak diizinkan."},
    "unknown_command": {
        EN: "Unknown command. Try /help",
        ID: "Perintah tidak dikenal. Coba /help",
    },
}


def t(key: str, lang: str = EN, **kw) -> str:
    row = S.get(key)
    if not row:
        return key
    text = row.get(lang) or row.get(EN) or key
    return text.format(**kw) if kw else text


def label(text: str, lang: str = EN) -> str:
    """Translate a value that arrives as an English label (session names)."""
    return t(text, lang)
