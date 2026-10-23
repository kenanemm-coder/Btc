"""Muhafazakâr dolum modelli, tek pozisyonlu spot simülasyonu.

Sıra (her mum i için, kronolojik):
  1) Bekleyen ÇIKIŞ varsa i. mum AÇILIŞINDA uygulanır.
  2) Bekleyen GİRİŞ varsa (pozisyon yokken) i. mum AÇILIŞINDA uygulanır; stop = açılış - k*ATR(sinyal mumu).
  3) Stop kontrolü: mumun düşüğü stop seviyesine değdiyse (giriş mumu dahil) tetiklenmiş sayılır;
     dolum referansı = min(stop, mum açılışı) (boşlukta açılış). Mum içi sıralama bilinmediği için iyimser varsayım yok.
  4) Mum KAPANIŞINDA: takip eden stop yalnızca kapanmış mumlarla güncellenir; sinyaller üretilir
     ve (1 + delay) mum sonrası açılışta uygulanmak üzere sıraya alınır.
Maliyet: alış = ref*(1+slipaj), komisyon tutardan; satış = ref*(1-slipaj), komisyon tutardan.
Değerleme: açık pozisyon, tahmini çıkış maliyetleri düşülerek (canlı botla aynı).
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Costs:
    fee_buy: float = 0.001
    fee_sell: float = 0.001
    slip_buy: float = 0.0005
    slip_sell: float = 0.0005
    delay_bars: int = 0


def costs_from(d):
    return Costs(**d)


ZERO = Costs(0, 0, 0, 0, 0)


def backtest(open_, high, low, close, entry, exit_sig, costs, atr=None, init_k=None, trail_k=None,
             start_idx=0):
    n = len(close)
    cash, qty = 1.0, 0.0
    pos = None
    pend_entry = pend_exit = None  # (yürütme_indeksi, sinyal_indeksi)
    eq = np.ones(n)
    held = np.zeros(n, dtype=bool)
    trades = []
    stop = trail_hi = None
    mark = (1 - costs.slip_sell) * (1 - costs.fee_sell)

    def close_pos(i, ref, reason, sig_i):
        nonlocal cash, qty, pos, stop, trail_hi
        fill = ref * (1 - costs.slip_sell)
        gross = qty * fill
        fee = gross * costs.fee_sell
        proceeds = gross - fee
        trades.append({
            "entry_i": pos["entry_i"], "exit_i": i, "sig_entry_i": pos["sig_i"], "sig_exit_i": sig_i,
            "cost": pos["cost"], "proceeds": proceeds, "ret": proceeds / pos["cost"] - 1.0,
            "reason": reason, "fees": pos["fee"] + fee,
            "slip": pos["slip"] + qty * (ref - fill), "entry_ref": pos["ref"], "exit_ref": ref,
        })
        cash, qty, pos, stop, trail_hi = proceeds, 0.0, None, None, None

    for i in range(start_idx, n):
        # 1) bekleyen çıkış (açılışta)
        if pend_exit is not None and pend_exit[0] == i:
            if pos is not None:
                close_pos(i, open_[i], "sinyal", pend_exit[1])
            pend_exit = None
        # 2) bekleyen giriş (açılışta)
        if pend_entry is not None and pend_entry[0] == i:
            if pos is None and cash > 0:
                ref = open_[i]
                fill = ref * (1 + costs.slip_buy)
                notional = cash / (1 + costs.fee_buy)
                qty = notional / fill
                a = atr[pend_entry[1]] if atr is not None else None
                pos = {"entry_i": i, "sig_i": pend_entry[1], "cost": cash, "ref": ref,
                       "fee": notional * costs.fee_buy, "slip": qty * (fill - ref)}
                stop = ref - init_k * a if (init_k is not None and a is not None) else None
                trail_hi = close[pend_entry[1]]  # girişten önceki son kapanış; sonra güncellenir
                cash = 0.0
            pend_entry = None
        # 3) stop kontrolü (muhafazakâr)
        if pos is not None and stop is not None and low[i] <= stop:
            close_pos(i, min(stop, open_[i]), "stop", None)
        # 4) kapanış: değerleme, stop güncelleme, sinyaller
        if pos is None:
            eq[i] = cash
        else:
            held[i] = True
            eq[i] = qty * close[i] * mark
            if trail_k is not None and atr is not None and not np.isnan(atr[i]):
                trail_hi = max(trail_hi, close[i])
                lvl = trail_hi - trail_k * atr[i]
                stop = lvl if stop is None else max(stop, lvl)
        ex_i = i + 1 + costs.delay_bars
        if ex_i < n:
            if pos is not None and pend_exit is None and exit_sig[i]:
                pend_exit = (ex_i, i)
            elif pos is None and pend_entry is None and pend_exit is None and entry[i]:
                pend_entry = (ex_i, i)
    return {"eq": eq, "held": held, "trades": trades, "open": pos is not None}
