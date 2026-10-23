"""Paper B: TEK adayın (B: 4s Donchian N=40, ATR k=3.0 takip eden stop) ileriye dönük kâğıt izlemesi.

Gerçek emir YOK, API anahtarı YOK, bildirim YOK. Yalnızca Binance genel mum verisi (GET) okunur.
Canlı bottan (paper/runner.py, 'data' dalı, bot.yml) tamamen ayrıdır; ona hiçbir şey yazmaz.

YÖNTEM (dürüst not): strategy_lab.backtest.backtest bir pozisyonla "kaldığı yerden devam" edemez
(nakit=1, pozisyon yok ile başlar) ve değiştirilmemelidir. Bu yüzden her çalışmada, sabit başlangıç mumundan
(START_OPEN_MS) bu yana YALNIZCA ileri tarihli tamamlanmış mumlar, değiştirilmemiş backtest ile deterministik
yeniden oynatılır; sonuçlar durum dosyasına EKLEME-YALNIZ defter olarak işlenir:
  - Defterdeki her kayıt kimliğiyle (giriş mumunun açılış zamanı) tekildir; aynı işlem iki kez eklenemez.
  - Daha önce kaydedilmiş bir işlem/özkaynak satırı yeniden oynatmada farklı çıkarsa (veri revizyonu, kod
    değişikliği) DURUR; geçmişi sessizce yeniden yazmaz.
Dolum mantığı strategy_lab ile birebir aynıdır (yeni bir dolum modeli yoktur).

Kullanım:  python -m paper_b.run --state data_b/paper_b_state.json [--init] [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

from paper.binance import BinanceClient, DataError
from research.data import fetch_klines
from strategy_lab import strategies
from strategy_lab.backtest import backtest, costs_from

SYMBOL = "BTCTRY"
TF = "4h"
BAR_MS = 14_400_000
# 2026-10-23 04:00 UTC = dondurulmuş test döneminin bittiği an. Bu açılış zamanlı mum ilk izlenen mumdur.
START_OPEN_MS = 1_792_728_000_000
PARAMS = {"entry_n": 40, "atr_k": 3.0, "exit": "trail"}
# Maliyet varsayımları SABİT (strategy_lab.spec 'temel' ile aynı; testte eşitlik doğrulanır).
COSTS = {"fee_buy": 0.0010, "fee_sell": 0.0010, "slip_buy": 0.0005, "slip_sell": 0.0005, "delay_bars": 0}
WARMUP_BARS = 400  # SMA200 + Donchian + ATR ısınması için başlangıçtan önce çekilen mum
VERSION = 1
TOL = 1e-9


class PaperBError(Exception):
    """Güvenli durma: durum dosyasına HİÇBİR ŞEY yazılmaz."""


def config_dict(start_open_ms=START_OPEN_MS):
    return {"symbol": SYMBOL, "tf": TF, "start_open_ms": start_open_ms, "params": dict(PARAMS), "costs": dict(COSTS)}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_data(base_url, start_open_ms, client=None):
    om, o, h, l, c = fetch_klines(base_url, SYMBOL, TF, BAR_MS, start_open_ms - WARMUP_BARS * BAR_MS,
                                  None, client=client, log=lambda *_: None)
    d = {"open_ms": om, "open": o, "high": h, "low": l, "close": c}
    steps = np.diff(om)
    if len(steps) and np.any(steps != BAR_MS):
        bad = int(np.flatnonzero(steps != BAR_MS)[0])
        raise PaperBError(f"Mum serisinde boşluk/sıra hatası: {iso(int(om[bad]))} sonrası. Durduruldu.")
    if int(om[0]) % BAR_MS != 0:
        raise PaperBError("Mum zamanları 4 saatlik ızgarada değil")
    return d


def replay(d, start_open_ms):
    """Değiştirilmemiş strategy_lab.backtest ile, başlangıç mumundan itibaren ileri tarihli yeniden oynatma."""
    om = d["open_ms"]
    start_idx = int(np.searchsorted(om, start_open_ms, side="left"))
    if start_idx >= len(om):
        return None  # henüz tamamlanmış ileri tarihli mum yok
    if start_idx < 250:
        raise PaperBError(f"Isınma verisi yetersiz ({start_idx} mum)")
    sig = strategies.make_b(d, TF, PARAMS["entry_n"], PARAMS["atr_k"], PARAMS["exit"])
    res = backtest(d["open"], d["high"], d["low"], d["close"], sig.entry, sig.exit_sig, costs_from(COSTS),
                   sig.atr, sig.init_k, sig.trail_k, start_idx)
    return {"res": res, "sig": sig, "start_idx": start_idx}


def trade_records(rp, d):
    om = d["open_ms"]
    out = []
    for t in rp["res"]["trades"]:
        out.append({"id": str(int(om[t["entry_i"]])), "entry_open_ms": int(om[t["entry_i"]]),
                    "exit_open_ms": int(om[t["exit_i"]]), "entry_ref": float(t["entry_ref"]),
                    "exit_ref": float(t["exit_ref"]), "ret": float(t["ret"]), "reason": t["reason"]})
    return out


def equity_rows(rp, d):
    om = d["open_ms"]
    eq = rp["res"]["eq"]
    return [[int(om[i]), float(eq[i])] for i in range(rp["start_idx"], len(om))]


def new_state(start_open_ms=START_OPEN_MS):
    return {"version": VERSION, "config": config_dict(start_open_ms), "last_bar_open_ms": None,
            "trades": [], "equity_log": [], "open_position": None, "pending_entry": False,
            "updated_utc": None}


def _same(a, b):
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


def merge(state, rp, d):
    """Eklemeli birleştirme. Dönüş: (yeni_işlem_sayısı, yeni_mum_sayısı). Tutarsızlıkta PaperBError."""
    new_tr = trade_records(rp, d)
    by_new = {t["id"]: t for t in new_tr}
    led = {t["id"]: t for t in state["trades"]}
    for tid, old in led.items():
        cur = by_new.get(tid)
        if cur is None:
            raise PaperBError(f"Defterdeki işlem yeniden oynatmada yok (geçmiş değişmiş): {tid}")
        for k in ("entry_open_ms", "exit_open_ms"):
            if old[k] != cur[k]:
                raise PaperBError(f"İşlem {tid} {k} farklı")
        for k in ("entry_ref", "exit_ref", "ret"):
            if not _same(old[k], cur[k]):
                raise PaperBError(f"İşlem {tid} {k} farklı: {old[k]} != {cur[k]}")
    added_tr = [t for t in new_tr if t["id"] not in led]
    # özkaynak günlüğü
    rows = equity_rows(rp, d)
    have = {r[0]: r[1] for r in state["equity_log"]}
    for ms, v in rows:
        if ms in have and not _same(have[ms], v):
            raise PaperBError(f"Özkaynak satırı {iso(ms)} farklı: {have[ms]} != {v}")
    replay_ms = {ms for ms, _ in rows}
    for ms in have:
        if ms not in replay_ms:
            raise PaperBError(f"Günlükteki mum yeniden oynatmada yok: {iso(ms)}")
    added_rows = [[ms, v] for ms, v in rows if ms not in have]
    state["trades"].extend(added_tr)
    state["equity_log"].extend(added_rows)
    return len(added_tr), len(added_rows)


def snapshot(state, rp, d):
    om = d["open_ms"]
    n = len(om)
    state["last_bar_open_ms"] = int(om[n - 1])
    res = rp["res"]
    if res["open"]:
        held = res["held"]
        j = n - 1
        while j > rp["start_idx"] and held[j - 1]:
            j -= 1
        state["open_position"] = {"entry_open_ms": int(om[j]), "entry_ref": float(d["open"][j])}
    else:
        state["open_position"] = None
    sig = rp["sig"]
    state["pending_entry"] = bool((not res["open"]) and sig.entry[n - 1])


def summary(state):
    eq = state["equity_log"][-1][1] if state["equity_log"] else 1.0
    tr = state["trades"]
    return {"son_mum_acilis": None if state["last_bar_open_ms"] is None else iso(state["last_bar_open_ms"]),
            "mum_sayisi": len(state["equity_log"]), "kapanan_islem": len(tr),
            "toplam_getiri_pct": (eq - 1.0) * 100.0, "acik_pozisyon": state["open_position"],
            "bekleyen_giris": state["pending_entry"]}


def load_state(path, init, start_open_ms):
    if not os.path.exists(path):
        if not init:
            raise PaperBError("Durum dosyası yok ve --init verilmedi; yeni deney kurulmadı.")
        return new_state(start_open_ms)
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    if st.get("version") != VERSION:
        raise PaperBError(f"Bilinmeyen durum sürümü: {st.get('version')}")
    if st.get("config") != config_dict(start_open_ms):
        raise PaperBError("Durum dosyasındaki ayarlar (başlangıç, parametre, maliyet) koddakilerle uyuşmuyor. "
                          "Deney ayarları sabittir; durduruldu.")
    return st


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run_once(state_path, base_url, init=False, dry_run=False, client=None, start_open_ms=START_OPEN_MS, now_utc=None):
    state = load_state(state_path, init, start_open_ms)
    d = load_data(base_url, start_open_ms, client=client)
    rp = replay(d, start_open_ms)
    added_tr = added_rows = 0
    if rp is not None:
        added_tr, added_rows = merge(state, rp, d)
        snapshot(state, rp, d)
    state["updated_utc"] = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not dry_run:
        save_state(state_path, state)
    return state, {"yeni_islem": added_tr, "yeni_mum": added_rows, **summary(state)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base-url", default=os.environ.get("BINANCE_BASE_URL") or "https://data-api.binance.vision")
    a = ap.parse_args(argv)
    try:
        _, info = run_once(a.state, a.base_url, a.init, a.dry_run)
    except (PaperBError, DataError) as e:
        print(f"HATA (durum dosyası değiştirilmedi): {e}", file=sys.stderr)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
