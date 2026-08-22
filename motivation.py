"""
/motivation
===========

Lines about discipline and risk, not about winning. A signal bot that cheers
you on to trade more is working against you, so nothing here says "go get
it" — the whole set is about protecting capital, sizing down, and being
willing to do nothing.

Randomised without repeating the previous pick for a given user, because the
same sentence twice in a row reads like a broken bot.
"""
from __future__ import annotations

import random

import i18n

LINES: list[dict[str, str]] = [
    {"en": "Discipline protects your capital when confidence fails.",
     "id": "Disiplin melindungi modal Anda saat keyakinan runtuh."},
    {"en": "One good trade is better than ten forced trades.",
     "id": "Satu trade yang bagus lebih baik daripada sepuluh trade yang dipaksakan."},
    {"en": "Protect your downside first; profits come second.",
     "id": "Lindungi sisi rugi dulu; profit urusan kedua."},
    {"en": "No setup is also a position.",
     "id": "Tidak ada setup juga merupakan sebuah posisi."},
    {"en": "The market pays for patience, not for effort.",
     "id": "Pasar membayar kesabaran, bukan kerja keras."},
    {"en": "Your stop is a decision you make before you have feelings about it.",
     "id": "Stop loss adalah keputusan yang Anda buat sebelum emosi ikut campur."},
    {"en": "Size so that being wrong is survivable, not just tolerable.",
     "id": "Atur lot agar salah tetap bisa bertahan, bukan sekadar tertahankan."},
    {"en": "Missing a move costs nothing. Chasing one costs money.",
     "id": "Melewatkan pergerakan tidak merugikan. Mengejarnya merugikan."},
    {"en": "A plan you abandon under pressure was never a plan.",
     "id": "Rencana yang Anda tinggalkan saat tertekan bukanlah rencana."},
    {"en": "Consistency beats intensity. Trade the twentieth setup like the first.",
     "id": "Konsistensi mengalahkan intensitas. Perlakukan setup ke-20 seperti yang pertama."},
    {"en": "Revenge trading turns one loss into a losing week.",
     "id": "Balas dendam pada pasar mengubah satu kerugian menjadi satu minggu rugi."},
    {"en": "The edge is small and slow. Leverage does not make it bigger, only louder.",
     "id": "Edge itu kecil dan lambat. Leverage tidak membuatnya besar, hanya berisik."},
    {"en": "Write the exit before the entry.",
     "id": "Tulis rencana keluar sebelum masuk."},
    {"en": "Drawdown is the price of admission, not a sign you are broken.",
     "id": "Drawdown adalah harga masuk, bukan tanda Anda gagal."},
    {"en": "If you cannot explain the setup in one sentence, you do not have one.",
     "id": "Jika setup tidak bisa dijelaskan dalam satu kalimat, berarti belum ada setup."},
]

_last: dict[int, int] = {}


def pick(user_id: int = 0, lang: str = i18n.EN) -> str:
    choices = [i for i in range(len(LINES)) if i != _last.get(user_id)]
    idx = random.choice(choices or list(range(len(LINES))))
    _last[user_id] = idx
    return LINES[idx].get(lang) or LINES[idx]["en"]
