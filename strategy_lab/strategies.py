"""Aday stratejiler: kapanmış mumlardan giriş/çıkış sinyali ve stop parametreleri üretir.

Sinyal t mumunun KAPANIŞINDA oluşur; dolum motorda t+1(+gecikme) açılışında yapılır.
"""
from dataclasses import dataclass

import numpy as np

from research import engine as RE

from . import indicators as I
from . import spec

BAR_MS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000}


@dataclass
class Signals:
    name: str
    family: str
    tf: str
    params: dict
    entry: np.ndarray            # bool: t kapanışında alış sinyali
    exit_sig: np.ndarray         # bool: t kapanışında çıkış sinyali (pozisyon varsa)
    atr: np.ndarray = None       # ATR (stop mesafesi için)
    init_k: float = None         # başlangıç stopu çarpanı (giriş açılışı - k*ATR)
    trail_k: float = None        # takip eden stop çarpanı (en yüksek kapanış - k*ATR)


def _valid(open_ms, tf, need):
    return RE.contiguous_run(open_ms, BAR_MS[tf]) >= need


def make_b(d, tf, entry_n, atr_k, exit_kind):
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    a = I.atr(h, l, c, spec.ATR_PERIOD)
    s200 = I.sma(c, spec.TREND_SMA)
    hi = I.prev_max(h, entry_n)
    lo10 = I.prev_min(l, 10)
    need = max(spec.TREND_SMA, entry_n + 1, spec.ATR_PERIOD + 1)
    ok = _valid(d["open_ms"], tf, need) & ~np.isnan(a) & ~np.isnan(s200) & ~np.isnan(hi)
    entry = ok & (c > s200) & (c > hi)
    ex = (~np.isnan(lo10)) & (c < lo10) if exit_kind == "chan10" else np.zeros(len(c), dtype=bool)
    return Signals(f"B[{tf} N={entry_n} k={atr_k} {exit_kind}]", "B", tf,
                   {"entry_n": entry_n, "atr_k": atr_k, "exit": exit_kind}, entry, ex, a, atr_k,
                   atr_k if exit_kind == "trail" else None)


def make_c(d, tf, fast, slow):
    c = d["close"]
    ef, es = I.ema(c, fast), I.ema(c, slow)
    ok = _valid(d["open_ms"], tf, slow) & ~np.isnan(ef) & ~np.isnan(es)
    up = ef > es
    prev_up = np.concatenate([[False], up[:-1]])
    entry = ok & up & ~prev_up
    ex = ok & (ef < es)
    return Signals(f"C[{tf} EMA{fast}/{slow}]", "C", tf, {"pair": (fast, slow)}, entry, ex)


def make_d(d, tf, a, b):
    c = d["close"]
    ra, rb = I.ret(c, a), I.ret(c, b)
    ok = _valid(d["open_ms"], tf, b + 1) & ~np.isnan(ra) & ~np.isnan(rb)
    entry = ok & (ra > 0) & (rb > 0)
    ex = ok & (ra <= 0)
    return Signals(f"D[{tf} mom{a}/{b}]", "D", tf, {"pair": (a, b)}, entry, ex)


def make_a(d):
    buy, sell = RE.signals(d["close"], d["open_ms"])
    return Signals("A[5m Bollinger+RSI+SMA50]", "A", "5m", {}, buy, sell)


def make_e(d, tf, start_idx):
    """Kontrol: değerlendirme başlangıcında al, sonda değerle (aynı maliyet ve değerleme)."""
    n = len(d["close"])
    entry = np.zeros(n, dtype=bool)
    entry[start_idx] = True
    return Signals(f"E[al-tut {tf}]", "E", tf, {}, entry, np.zeros(n, dtype=bool))


def all_candidates(data):
    """data: {'1h': dict, '4h': dict, '5m': dict} -> ön kayıtlı 26 aday (A:1, B:16, C:4, D:4, E:1)."""
    out = []
    for tf in spec.TIMEFRAMES:
        d = data[tf]
        for n in spec.B_GRID["entry_n"]:
            for k in spec.B_GRID["atr_k"]:
                for ex in spec.B_GRID["exit"]:
                    out.append(make_b(d, tf, n, k, ex))
        for f, s in spec.C_GRID["pair"]:
            out.append(make_c(d, tf, f, s))
        for a, b in spec.D_GRID["pair"]:
            out.append(make_d(d, tf, a, b))
    out.append(make_a(data["5m"]))
    d4 = data["4h"]
    start_idx = int(np.searchsorted(d4["open_ms"], data["_eval_start_ms"], side="left"))
    out.append(make_e(d4, "4h", start_idx))
    return out
