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
    "no_odds_timed": {
        EN: "no target, so no odds quoted",
        ID: "tanpa target, jadi peluang tidak dihitung",
    },
    "order_fade": {
        EN: "{z} sigma stretched — taken at market",
        ID: "Terentang {z} sigma — masuk di harga pasar",
    },
    "time_exit": {
        EN: "Exit after {bars} bars (~{dur}) at market — no target",
        ID: "Keluar setelah {bars} bar (~{dur}) di harga pasar — tanpa target",
    },
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

    # ---------------------------------------------------------- performance
    "performance": {EN: "PERFORMANCE", ID: "PERFORMA"},
    "trades": {EN: "Trades", ID: "Transaksi"},
    "pairs": {EN: "Pairs", ID: "Pair"},
    "profit": {EN: "Profit", ID: "Profit"},
    "loss": {EN: "Loss", ID: "Rugi"},
    "net": {EN: "Net", ID: "Bersih"},
    "total_r": {EN: "Total R", ID: "Total R"},
    "total_points": {EN: "Total Points", ID: "Total Poin"},
    "no_trades_period": {
        EN: "No settled trades in this period yet.",
        ID: "Belum ada transaksi selesai pada periode ini.",
    },

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

    # ------------------------------------------------------ lifecycle (§18)
    "ev_entry": {EN: "ENTRY HIT", ID: "ENTRY TERISI"},
    "ev_tp": {EN: "TP{n} HIT", ID: "TP{n} KENA"},
    "ev_breakeven": {EN: "MOVE TO BREAKEVEN", ID: "PINDAHKAN KE BREAKEVEN"},
    "ev_stop": {EN: "STOP LOSS HIT", ID: "KENA STOP LOSS"},
    "ev_complete": {EN: "ALL TARGETS HIT", ID: "SEMUA TARGET TERCAPAI"},
    "ev_breakeven_note": {
        EN: "First target paid — the rest of the position now rides at zero risk.",
        ID: "Target pertama tercapai — sisa posisi kini tanpa risiko.",
    },
    "next_target": {EN: "Next target:", ID: "Target berikutnya:"},
    "result": {EN: "Result", ID: "Hasil"},

    # ---------------------------------------------------------------- help
    "sec_signals": {EN: "Signals", ID: "Sinyal"},
    "sec_performance": {EN: "Performance", ID: "Performa"},
    "sec_risk": {EN: "Risk Management", ID: "Manajemen Risiko"},
    "sec_settings": {EN: "Settings", ID: "Pengaturan"},
    "sec_other": {EN: "Other", ID: "Lainnya"},
    "help_footer": {
        EN: ("Answers are ENTRY, WAIT or NO TRADE. Confidence is how well the "
             "strategy's conditions line up; Odds is the chance of a target "
             "paying before the stop; Exp is that in R. /calibration says "
             "whether those odds were measured on real trades or are still "
             "the model's estimate."),
        ID: ("Jawaban berupa ENTRY, WAIT, atau NO TRADE. Keyakinan menunjukkan "
             "seberapa cocok syarat strategi; Peluang adalah kemungkinan target "
             "tercapai sebelum stop; Ekspektasi menyatakannya dalam R. "
             "/calibration menunjukkan apakah peluang itu terukur dari trade "
             "nyata atau masih estimasi model."),
    },

    # --------------------------------------------------------------- menus
    "settings_title": {EN: "YOUR SETTINGS", ID: "PENGATURAN ANDA"},
    "status_title": {EN: "STATUS", ID: "STATUS"},
    "history_title": {EN: "RECENT TRADES", ID: "TRANSAKSI TERAKHIR"},
    "strategies_title": {EN: "BENIHANA STRATEGIES", ID: "STRATEGI BENIHANA"},
    "strategy": {EN: "Strategy", ID: "Strategi"},
    "language": {EN: "Language", ID: "Bahasa"},
    "min_conf": {EN: "Minimum confidence", ID: "Keyakinan minimum"},
    "default_risk": {EN: "Default risk", ID: "Risiko default"},
    "management": {EN: "Risk management", ID: "Manajemen risiko"},
    "on": {EN: "on", ID: "aktif"},
    "off": {EN: "off", ID: "nonaktif"},
    "not_set": {EN: "not set", ID: "belum diatur"},
    "active_signals": {EN: "Active signals", ID: "Sinyal aktif"},
    "trades_today": {EN: "Trades today", ID: "Transaksi hari ini"},
    "balance": {EN: "Balance", ID: "Saldo"},
    "day_pl": {EN: "Today's P/L", ID: "P/L hari ini"},
    "profit_target": {EN: "Profit target", ID: "Target profit"},
    "no_active": {
        EN: "No active signals right now.",
        ID: "Tidak ada sinyal aktif saat ini.",
    },
    "no_history": {
        EN: "No settled trades yet.",
        ID: "Belum ada transaksi yang selesai.",
    },
    "strategy_selected": {
        EN: "selected. All new signals will use it.",
        ID: "dipilih. Semua sinyal baru akan memakainya.",
    },
    "strategy_howto": {
        EN: "Select one, e.g. <code>/strategy 1</code>",
        ID: "Pilih salah satu, misalnya <code>/strategy 1</code>",
    },

    # ------------------------------------------------------------ /update
    "upd_title": {EN: "SIGNAL TRACKER", ID: "PELACAK SINYAL"},
    "upd_swing_title": {EN: "SWING TRACKER", ID: "PELACAK SWING"},
    "upd_none": {
        EN: "No signals being tracked. Ask for one with /signal.",
        ID: "Belum ada sinyal yang dilacak. Minta satu dengan /signal.",
    },
    "upd_none_swing": {
        EN: "No swing signals running. /signal xauusd swing starts one.",
        ID: "Belum ada sinyal swing berjalan. /signal xauusd swing untuk memulai.",
    },
    "upd_live": {EN: "Live", ID: "Berjalan"},
    "upd_done": {EN: "Finished", ID: "Selesai"},
    "upd_hint": {
        EN: "Updates arrive when the tracker runs. On a free host that is "
            "once a day — see /help.",
        ID: "Pembaruan masuk saat pelacak berjalan. Di host gratis sekali "
            "sehari — lihat /help.",
    },

    # ---------------------------------------------------------- /resetdata
    "dt_now": {EN: "now", ID: "sekarang"},
    "dt_in_dh": {EN: "in {d}d {h}h", ID: "{d} hari {h} jam lagi"},
    "dt_in_hm": {EN: "in {h}h {m}m", ID: "{h} jam {m} menit lagi"},
    "dt_in_m": {EN: "in {m}m", ID: "{m} menit lagi"},
    "st_no_filter": {EN: "no filter", ID: "tanpa filter"},
    "st_today": {
        EN: "Today: <b>{n}</b> closed", ID: "Hari ini: <b>{n}</b> selesai",
    },
    "st_nothing_today": {
        EN: "Nothing closed today yet",
        ID: "Belum ada yang selesai hari ini",
    },
    "st_trades": {EN: "Trades", ID: "Trade"},
    "st_drawdown": {EN: "Drawdown", ID: "Drawdown"},
    "st_target": {EN: "Target", ID: "Target"},
    "st_set_limits": {
        EN: "<i>/management on to set limits</i>",
        ID: "<i>/management on untuk mengatur batas</i>",
    },
    "hist_summary": {
        EN: "<i>{w}W · {l}L · {r}R over the last {n}</i>",
        ID: "<i>{w}M · {l}K · {r}R dari {n} terakhir</i>",
    },
    "sym_title": {EN: "🗺 <b>TRADABLE MARKETS</b>", ID: "🗺 <b>PASAR TERSEDIA</b>"},
    "sym_cost": {EN: "cost to trade", ID: "biaya transaksi"},
    "sym_nicknames": {
        EN: ("<i>Nicknames work: gold, cable, guppy, kiwi, aussie, fiber.</i>\n"
             "<i>Crypto, indices and energy are excluded — they need a paid "
             "data plan.</i>"),
        ID: ("<i>Nama panggilan bisa: gold, cable, guppy, kiwi, aussie, "
             "fiber.</i>\n<i>Kripto, indeks dan energi tidak tersedia — "
             "perlu paket data berbayar.</i>"),
    },
    "conf_title": {
        EN: "🎯 <b>Confidence filter set</b>",
        ID: "🎯 <b>Filter keyakinan diatur</b>",
    },
    "conf_off_title": {
        EN: "🎯 <b>Confidence filter off</b>",
        ID: "🎯 <b>Filter keyakinan nonaktif</b>",
    },
    "conf_band_relaxed": {
        EN: "🟩 relaxed — most setups pass",
        ID: "🟩 longgar — hampir semua setup lolos",
    },
    "conf_band_balanced": {EN: "🟨 balanced", ID: "🟨 seimbang"},
    "conf_band_strict": {
        EN: "🟧 strict — expect few signals",
        ID: "🟧 ketat — sinyal akan jarang",
    },
    "conf_band_very_strict": {
        EN: "🟥 very strict — you may see nothing for days",
        ID: "🟥 sangat ketat — bisa berhari-hari tanpa sinyal",
    },
    "sub_yours": {EN: "🎟 <b>YOUR ACCESS</b>", ID: "🎟 <b>AKSES ANDA</b>"},
    "sub_owner": {
        EN: "👑 Owner — unlimited, never expires.",
        ID: "👑 Pemilik — tanpa batas, tidak pernah kedaluwarsa.",
    },
    "sub_active": {
        EN: "✅ Active until <b>{until}</b>\n⏳ {days} days left",
        ID: "✅ Aktif sampai <b>{until}</b>\n⏳ sisa {days} hari",
    },
    "sub_expired": {
        EN: ("⛔ No active subscription.\n\nAsk the owner for access and "
             "give them your ID: <code>{uid}</code>"),
        ID: ("⛔ Tidak ada langganan aktif.\n\nMinta akses ke pemilik dan "
             "berikan ID Anda: <code>{uid}</code>"),
    },
    "sub_soon": {
        EN: "⚠️ Your access ends in {days} days.",
        ID: "⚠️ Akses Anda berakhir dalam {days} hari.",
    },
    "sub_denied": {
        EN: ("⛔ <b>Subscription required</b>\n\nThis bot is private. Send "
             "your ID to the owner to get access:\n<code>{uid}</code>"),
        ID: ("⛔ <b>Perlu langganan</b>\n\nBot ini privat. Kirim ID Anda ke "
             "pemilik untuk mendapatkan akses:\n<code>{uid}</code>"),
    },
    "sub_granted": {
        EN: ("✅ Granted <b>{days}</b> days to <code>{uid}</code>\n"
             "Now active until <b>{until}</b>"),
        ID: ("✅ Memberikan <b>{days}</b> hari ke <code>{uid}</code>\n"
             "Aktif sampai <b>{until}</b>"),
    },
    "sub_revoked": {
        EN: "🚫 Access removed for <code>{uid}</code>.",
        ID: "🚫 Akses dicabut untuk <code>{uid}</code>.",
    },
    "sub_nothing": {
        EN: "Nothing to revoke — <code>{uid}</code> had no subscription.",
        ID: "Tidak ada yang dicabut — <code>{uid}</code> tidak berlangganan.",
    },
    "sub_grant_usage": {
        EN: ("Usage: <code>/grant &lt;user_id&gt; &lt;days&gt; [plan]</code>\n"
             "Example: <code>/grant 123456789 30 standard</code>"),
        ID: ("Cara: <code>/grant &lt;user_id&gt; &lt;hari&gt; [paket]</code>\n"
             "Contoh: <code>/grant 123456789 30 standard</code>"),
    },
    "sub_list_empty": {
        EN: "No subscriptions on record.",
        ID: "Belum ada langganan tercatat.",
    },
    "sub_owner_only": {
        EN: "That command is for the owner only.",
        ID: "Perintah itu hanya untuk pemilik.",
    },
    "sub_off": {
        EN: ("<i>Subscriptions are switched off — everyone allowed by "
             "ALLOWED_USER_IDS can use the bot. Set "
             "SUBSCRIPTIONS_ENABLED=1 to turn them on.</i>"),
        ID: ("<i>Langganan sedang nonaktif — semua yang ada di "
             "ALLOWED_USER_IDS bisa memakai bot. Set "
             "SUBSCRIPTIONS_ENABLED=1 untuk mengaktifkan.</i>"),
    },
    "sec_open": {EN: "Open positions", ID: "Posisi terbuka"},
    "ev_nfp": {EN: "Non-farm payrolls", ID: "Non-farm payrolls"},
    "ev_nfp_note": {
        EN: "first Friday, 08:30 New York",
        ID: "Jumat pertama, 08:30 New York",
    },
    "ev_weekly_close": {EN: "Weekly close", ID: "Penutupan mingguan"},
    "ev_weekly_close_note": {
        EN: "spreads widen, then the weekend gap",
        ID: "spread melebar, lalu gap akhir pekan",
    },
    "news_title": {
        EN: "🗓 <b>EVENT RISK</b>", ID: "🗓 <b>RISIKO EVENT</b>",
    },
    "news_none": {
        EN: "Nothing scheduled in the next {days} days.",
        ID: "Tidak ada jadwal dalam {days} hari ke depan.",
    },
    "news_blackout": {
        EN: ("🚨 <b>{name}</b> is inside its window right now "
             "({before} min before to {after} min after).\n"
             "Spreads widen and stops get run. Trading through it is a "
             "coin flip with a worse price."),
        ID: ("🚨 <b>{name}</b> sedang dalam jendela rilis "
             "({before} menit sebelum sampai {after} menit sesudah).\n"
             "Spread melebar dan stop mudah kena. Trading saat ini seperti "
             "melempar koin dengan harga yang lebih buruk."),
    },
    "news_clear": {
        EN: "✅ No high-impact release inside its window right now.",
        ID: "✅ Tidak ada rilis berdampak tinggi saat ini.",
    },
    "news_tz_approx": {
        EN: ("⚠️ <i>This host has no timezone database, so release times "
             "are US Eastern Standard — correct in winter, one hour early "
             "during US daylight saving.</i>"),
        ID: ("⚠️ <i>Host ini tidak punya basis data zona waktu, jadi waktu "
             "rilis memakai US Eastern Standard — tepat saat musim dingin, "
             "satu jam lebih awal saat daylight saving AS.</i>"),
    },
    "news_howto": {
        EN: ("<i>The bot has no news feed. It computes what a calendar rule "
             "can prove — payrolls is always the first Friday — and reads "
             "anything else you add to events.json. It never guesses a "
             "date.</i>"),
        ID: ("<i>Bot ini tidak punya feed berita. Yang dihitung hanya yang "
             "bisa dipastikan dari aturan kalender — payrolls selalu Jumat "
             "pertama — ditambah apa pun yang Anda isi di events.json. Bot "
             "tidak pernah menebak tanggal.</i>"),
    },
    "reset_confirm": {
        EN: ("🗑 <b>Erase all your records?</b>\n\n"
             "This wipes every signal behind /stats, /daily, /weekly, "
             "/monthly, /history and /status. It cannot be undone.\n\n"
             "Send <code>/resetdata yes</code> to confirm."),
        ID: ("🗑 <b>Hapus semua catatan Anda?</b>\n\n"
             "Ini menghapus semua sinyal di balik /stats, /daily, /weekly, "
             "/monthly, /history dan /status. Tidak bisa dibatalkan.\n\n"
             "Kirim <code>/resetdata yes</code> untuk konfirmasi."),
    },
    "reset_done": {
        EN: ("🗑 <b>Records erased</b>\n\n{n} signals removed. /stats, "
             "/daily, /weekly, /monthly, /history and /status all start "
             "from zero.\n\n<i>Risk management settings were left alone — "
             "use /management off to clear those.</i>"),
        ID: ("🗑 <b>Catatan dihapus</b>\n\n{n} sinyal dihapus. /stats, "
             "/daily, /weekly, /monthly, /history dan /status mulai dari "
             "nol.\n\n<i>Pengaturan manajemen risiko tidak diubah — "
             "gunakan /management off untuk menghapusnya.</i>"),
    },
    "reset_empty": {
        EN: "Nothing to erase — no signals recorded yet.",
        ID: "Tidak ada yang dihapus — belum ada sinyal tercatat.",
    },

    # ------------------------------------------------------- management (§17)
    "mgmt_form": {
        EN: ("🛡️ <b>Risk and money management</b>\n\n"
             "Fill these in for me to manage your risk.\n\n"
             "1. Balance — e.g. <code>1000$</code> or <code>17000000IDR</code>\n"
             "2. Risk per trade — e.g. <code>1</code> (percent)\n"
             "3. Daily drawdown — e.g. <code>5</code> (percent)\n"
             "4. Max daily trades — e.g. <code>5</code>\n"
             "5. Profit target — e.g. <code>5</code> (percent)\n\n"
             "All in one line, same order:\n"
             "<code>/management on 1000$ 1 5 5 5</code>\n\n"
             "Turn it off with <code>/management off</code>"),
        ID: ("🛡️ <b>Manajemen risiko dan modal</b>\n\n"
             "Isi data berikut agar saya bisa mengatur risiko Anda.\n\n"
             "1. Saldo — misalnya <code>1000$</code> atau <code>17000000IDR</code>\n"
             "2. Risiko per transaksi — misalnya <code>1</code> (persen)\n"
             "3. Drawdown harian — misalnya <code>5</code> (persen)\n"
             "4. Maksimal transaksi harian — misalnya <code>5</code>\n"
             "5. Target profit — misalnya <code>5</code> (persen)\n\n"
             "Tulis dalam satu baris, urut:\n"
             "<code>/management on 1000$ 1 5 5 5</code>\n\n"
             "Matikan dengan <code>/management off</code>"),
    },
    "mgmt_on": {
        EN: ("🛡️ <b>Risk management is on</b>\n\n"
             "💰 Balance: {balance}\n"
             "⚖️ Risk per trade: {risk}% = {per_trade}\n"
             "📉 Daily drawdown limit: {dd}% ({dd_cash})\n"
             "🔢 Max daily trades: {trades}\n"
             "🎯 Profit target: +{target}% ({target_cash})\n\n"
             "<i>Signals will be sized from this, and will stop when a limit "
             "is reached.</i>"),
        ID: ("🛡️ <b>Manajemen risiko aktif</b>\n\n"
             "💰 Saldo: {balance}\n"
             "⚖️ Risiko per transaksi: {risk}% = {per_trade}\n"
             "📉 Batas drawdown harian: {dd}% ({dd_cash})\n"
             "🔢 Maksimal transaksi harian: {trades}\n"
             "🎯 Target profit: +{target}% ({target_cash})\n\n"
             "<i>Sinyal akan dihitung dari ini, dan berhenti saat batas "
             "tercapai.</i>"),
    },
    "mgmt_off": {
        EN: ("🛡️ <b>Risk and money management is off</b>\n\n"
             "Your balance, limits and daily counters have been cleared.\n\n"
             "<i>Set it up again with</i> <code>/management on ...</code>"),
        ID: ("🛡️ <b>Manajemen risiko dan modal nonaktif</b>\n\n"
             "Saldo, batas, dan penghitung harian Anda telah dihapus.\n\n"
             "<i>Aktifkan lagi dengan</i> <code>/management on ...</code>"),
    },
    "max_dd_today": {EN: "Worst drawdown today", ID: "Drawdown terburuk hari ini"},
    "mgmt_max_trades": {
        EN: ("🛡️ <b>Risk Management</b>\n\n"
             "Maximum daily trades reached: {n}/{max}.\n\n"
             "No additional signals will be provided today.\n\n"
             "Risk management is still active."),
        ID: ("🛡️ <b>Manajemen Risiko</b>\n\n"
             "Batas transaksi harian tercapai: {n}/{max}.\n\n"
             "Tidak ada sinyal tambahan hari ini.\n\n"
             "Manajemen risiko tetap aktif."),
    },
    "mgmt_drawdown": {
        EN: ("🛡️ <b>Risk Management</b>\n\n"
             "Daily drawdown limit reached: ${pl} against a ${limit} limit.\n\n"
             "No more signals today. Come back tomorrow."),
        ID: ("🛡️ <b>Manajemen Risiko</b>\n\n"
             "Batas drawdown harian tercapai: ${pl} dari batas ${limit}.\n\n"
             "Tidak ada sinyal lagi hari ini. Kembali besok."),
    },
    "mgmt_target_hit": {
        EN: ("🎯 <b>Profit Target Reached</b>\n\n"
             "Your profit target has been reached.\n\n"
             "📈 Target: +{target}%\n"
             "💰 Profit: +${profit}\n"
             "📊 Starting balance: ${start}\n\n"
             "🛡️ Risk and money management has been automatically turned off.\n\n"
             "No more signals will be provided under the current settings. "
             "Set it up again with <code>/management on ...</code> when you "
             "are ready."),
        ID: ("🎯 <b>Target Profit Tercapai</b>\n\n"
             "Target profit Anda telah tercapai.\n\n"
             "📈 Target: +{target}%\n"
             "💰 Profit: +${profit}\n"
             "📊 Saldo awal: ${start}\n\n"
             "🛡️ Manajemen risiko dan modal dimatikan otomatis.\n\n"
             "Tidak ada sinyal lagi dengan pengaturan saat ini. Aktifkan lagi "
             "dengan <code>/management on ...</code> bila Anda siap."),
    },
    "cooldown": {
        EN: ("⏳ A {mode} signal on {pair} is still running "
             "({state}). A new one comes after it finishes — or "
             "<code>/cancel {sid}</code> to drop it."),
        ID: ("⏳ Sinyal {mode} pada {pair} masih berjalan "
             "({state}). Sinyal baru menyusul setelah selesai — atau "
             "<code>/cancel {sid}</code> untuk membatalkannya."),
    },
    "cancelled_ok": {EN: "Signal cancelled.", ID: "Sinyal dibatalkan."},
    "cancel_notfound": {
        EN: "No active signal with that id.",
        ID: "Tidak ada sinyal aktif dengan id itu.",
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


DAYS = {
    EN: ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    ID: ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"),
}
MONTHS = {
    EN: ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    ID: ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
         "Jul", "Agu", "Sep", "Okt", "Nov", "Des"),
}


def date_short(dt, lang: str = EN) -> str:
    """'Fri 04 Sep' in the reader's language."""
    d = DAYS.get(lang, DAYS[EN])[dt.weekday()]
    m = MONTHS.get(lang, MONTHS[EN])[dt.month - 1]
    return f"{d} {dt.day:02d} {m}"


def date_long(dt, lang: str = EN) -> str:
    """'04 Sep 2026' in the reader's language."""
    m = MONTHS.get(lang, MONTHS[EN])[dt.month - 1]
    return f"{dt.day:02d} {m} {dt.year}"


def t(key: str, lang: str = EN, **kw) -> str:
    row = S.get(key)
    if not row:
        return key
    text = row.get(lang) or row.get(EN) or key
    return text.format(**kw) if kw else text


def label(text: str, lang: str = EN) -> str:
    """Translate a value that arrives as an English label (session names)."""
    return t(text, lang)
