"""Vektörel sinyal + canlı botla aynı ekonomiyi kullanan işlem simülasyonu.

Sinyal formülleri paper/indicators.py ile aynıdır (parite testi tests/test_research.py).
Maliyet modeli paper/portfolio.py ile aynıdır: tüm nakitle alım, tüm BTC ile satım, short yok,
alış fiyatı = ref*(1+slipaj), satış fiyatı = ref*(1-slipaj), komisyon tutar üzerinden.
Sinyal mumu kapanışında görülür; `latency` mum kadar sonraki kapanışta dolum varsayılır
(GitHub Actions gecikmesi için). latency=0 iyimserdir.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

BAR_MS = 300_000
DAY_MS = 86_400_000


@dataclass(frozen=True)
class Params:
    bb_n: int = 20
    bb_k: float = 2.0
    rsi_n: int = 14
    rsi_max: float = 48.0
    sma_n: int = 50


@dataclass(frozen=True)
class Costs:
    fee_buy: float = 0.001
    fee_sell: float = 0.001
    slip_buy: float = 0.0005
    slip_sell: float = 0.0005


ZERO = Costs(0, 0, 0, 0)
BASE = Costs()
DOUBLE = Costs(0.002, 0.002, 0.001, 0.001)


def contiguous_run(open_ms, interval_ms=BAR_MS):
    """Her mum için, o muma kadar kesintisiz ardışık mum sayısı (kendisi dahil)."""
    ok = np.diff(open_ms) == interval_ms
    run = np.ones(len(open_ms), dtype="int64")
    for i in range(1, len(open_ms)):
        run[i] = run[i - 1] + 1 if ok[i - 1] else 1
    return run


def signals(close, open_ms, p=Params(), interval_ms=BAR_MS):
    """(buy, sell) boolean dizileri. Pencere boşluklu veya yetersizse sinyal yok."""
    s = pd.Series(np.asarray(close, dtype="float64"))
    sma = s.rolling(p.bb_n).mean()
    std = s.rolling(p.bb_n).std(ddof=1)
    upper, lower = sma + p.bb_k * std, sma - p.bb_k * std
    sma_t = s.rolling(p.sma_n).mean()
    d = s.diff()
    gain = d.clip(lower=0).rolling(p.rsi_n).mean()
    loss = (-d.clip(upper=0)).rolling(p.rsi_n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    rsi = rsi.where(loss != 0, np.where(gain > 0, 100.0, np.nan))
    need = max(p.sma_n, p.bb_n, p.rsi_n + 1)
    valid = (contiguous_run(open_ms, interval_ms) >= need) & rsi.notna() & upper.notna() & sma_t.notna()
    valid = valid.to_numpy()
    c = s.to_numpy()
    buy = valid & (c <= lower.to_numpy()) & (rsi.to_numpy() < p.rsi_max) & (c > sma_t.to_numpy())
    sell = valid & (c >= upper.to_numpy()) & ~buy
    return buy, sell


def simulate(close, buy, sell, costs=BASE, latency=0, cash0=1.0):
    """Sıralı işlem simülasyonu. Dönüş: sonuç sözlüğü (işlemler + mum bazlı özkaynak)."""
    n = len(close)
    cash, qty, pos = cash0, 0.0, None
    trades = []
    for i in np.flatnonzero(buy | sell):
        j = i + latency
        if j >= n:
            break
        if pos is None and buy[i]:
            ref = close[j]
            fill = ref * (1 + costs.slip_buy)
            qty = cash / (1 + costs.fee_buy) / fill
            pos = {"sig": int(i), "entry": int(j), "cost": cash, "qty": qty, "ref": ref}
            cash = 0.0
        elif pos is not None and sell[i]:
            ref = close[j]
            proceeds = pos["qty"] * ref * (1 - costs.slip_sell) * (1 - costs.fee_sell)
            trades.append({"entry": pos["entry"], "exit": int(j), "sig_entry": pos["sig"],
                           "sig_exit": int(i), "cost": pos["cost"], "proceeds": proceeds,
                           "ret": proceeds / pos["cost"] - 1.0})
            cash, pos = proceeds, None
    eq = np.empty(n, dtype="float64")
    cur, c0 = 0, cash0
    mark = (1 - costs.slip_sell) * (1 - costs.fee_sell)
    for t in trades:
        eq[cur:t["entry"]] = c0
        q = t["cost"] / (1 + costs.fee_buy) / (close[t["entry"]] * (1 + costs.slip_buy))
        eq[t["entry"]:t["exit"]] = q * close[t["entry"]:t["exit"]] * mark
        cur, c0 = t["exit"], t["proceeds"]
    if pos is None:
        eq[cur:] = c0
        open_pos = None
    else:
        eq[cur:pos["entry"]] = c0
        eq[pos["entry"]:] = pos["qty"] * close[pos["entry"]:] * mark
        open_pos = pos
    return {"trades": trades, "eq": eq, "open": open_pos, "cash0": cash0}


def max_drawdown_pct(eq):
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak) * 100.0)


def trade_stats(trades):
    r = np.array([t["ret"] for t in trades], dtype="float64")
    if len(r) == 0:
        return {"n": 0, "win_rate": None, "avg_ret_pct": None, "avg_win_pct": None,
                "avg_loss_pct": None, "profit_factor": None, "sum_ret_pct": 0.0}
    w, l = r[r > 0], r[r < 0]
    return {
        "n": int(len(r)),
        "win_rate": float(len(w) / len(r) * 100.0),
        "avg_ret_pct": float(r.mean() * 100.0),
        "avg_win_pct": float(w.mean() * 100.0) if len(w) else None,
        "avg_loss_pct": float(l.mean() * 100.0) if len(l) else None,
        "profit_factor": float(w.sum() / -l.sum()) if len(l) else None,
        "sum_ret_pct": float(r.sum() * 100.0),
    }


def regime_labels(close, open_ms, lookback_days=90, up=0.15, down=-0.15):
    """Strateji sonuçlarından BAĞIMSIZ, yalnızca geçmişe bakan rejim etiketi:
    son `lookback_days` gün getirisi > up: 'yukselis', < down: 'dusus', arası 'yatay'.
    Isınma döneminde (ilk lookback_days) etiket None."""
    target = open_ms - lookback_days * DAY_MS
    idx = np.searchsorted(open_ms, target, side="right") - 1
    ok = (idx >= 0) & (open_ms - open_ms[np.maximum(idx, 0)] <= (lookback_days + 1) * DAY_MS)
    ret = np.where(ok, close / close[np.maximum(idx, 0)] - 1.0, np.nan)
    lab = np.full(len(close), None, dtype=object)
    lab[ok & (ret > up)] = "yukselis"
    lab[ok & (ret < down)] = "dusus"
    lab[ok & (ret >= down) & (ret <= up)] = "yatay"
    return lab, ret


def log_returns(series):
    s = np.asarray(series, dtype="float64")
    return np.concatenate([[0.0], np.diff(np.log(s))])
