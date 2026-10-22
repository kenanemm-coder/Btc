"""Sanal portföy ve işlem motoru (saf fonksiyonlar, state sözlüğü üzerinde).

Kurallar:
  - Spot: short yok. Pozisyon yokken SATIŞ sinyali yok sayılır.
  - Pozisyon varken ALIM sinyali yok sayılır (ikinci alım yok).
  - Alış: tüm TRY nakit ile. Fiyat = referans * (1 + slipaj); komisyon alım tutarı üzerinden.
  - Satış: tüm BTC. Fiyat = referans * (1 - slipaj); komisyon satış tutarı üzerinden.
  - Referans fiyat = sinyal mumunun kapanışı (VARSAYIM: sinyal anında bu fiyatın
    görülebildiği; tespit gecikmesi ayrıca slipaj ile modellenmez).
  - Değerleme: açık pozisyon, tahmini satış maliyetleri (slipaj + komisyon) düşülerek.
"""
from datetime import datetime, timezone

from . import MODE, STATE_VERSION


def iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")


def new_state(cfg, usdt_try_rate, now_ms, last_candle_open_ms, last_price, report_date):
    initial_try = cfg.initial_usdt * usdt_try_rate
    return {
        "version": STATE_VERSION,
        "mode": MODE,
        "symbol": cfg.symbol,
        "fx_symbol": cfg.fx_symbol,
        "created_at": iso(now_ms),
        "initial": {
            "usdt": cfg.initial_usdt,
            "usdt_try_rate": usdt_try_rate,
            "try": initial_try,
            "rate_time": iso(now_ms),
        },
        "cash_try": initial_try,
        "btc": 0.0,
        "position": None,
        "trades": [],
        "realized_pnl_try": 0.0,
        "last_candle_open_ms": last_candle_open_ms,
        "last_price": last_price,
        "peak_equity_try": initial_try,
        "max_drawdown_pct": 0.0,
        "daily_equity": [],
        "last_report_date": report_date,
        "skipped_stale_signals": 0,
        "gaps": [],
        "outbox": [],
        "next_outbox_id": 1,
    }


def liquidation_value_try(state, cfg, price):
    """Açık BTC'nin tahmini net satış geliri (slipaj + komisyon düşülmüş)."""
    return state["btc"] * price * (1 - cfg.slippage_sell) * (1 - cfg.fee_sell)


def equity_try(state, cfg, price):
    return state["cash_try"] + liquidation_value_try(state, cfg, price)


def unrealized_pnl_try(state, cfg, price):
    pos = state["position"]
    if not pos:
        return 0.0
    return liquidation_value_try(state, cfg, price) - pos["cost_try"]


def total_pnl_try(state, cfg, price):
    return equity_try(state, cfg, price) - state["initial"]["try"]


def open_buy(state, cfg, ref_price, candle_open_ms, signal_time_ms, detected_ms, reason):
    """Tüm nakitle sanal alış. Pozisyon varsa veya nakit yoksa None."""
    if state["position"] is not None or state["cash_try"] <= 0:
        return None
    cash = state["cash_try"]
    fill = ref_price * (1 + cfg.slippage_buy)
    notional = cash / (1 + cfg.fee_buy)
    qty = notional / fill
    fee = notional * cfg.fee_buy
    state["position"] = {
        "entry_candle_open_ms": candle_open_ms,
        "entry_time": iso(signal_time_ms),
        "detected_at": iso(detected_ms),
        "entry_ref_price": ref_price,
        "entry_price": fill,
        "qty": qty,
        "entry_fee_try": fee,
        "cost_try": cash,
        "reason": reason,
    }
    state["btc"] = qty
    state["cash_try"] = 0.0
    return dict(state["position"])


def close_position(state, cfg, ref_price, candle_open_ms, signal_time_ms, detected_ms, reason):
    """Açık pozisyonu sanal satışla kapatır. Pozisyon yoksa None (short açılmaz)."""
    pos = state["position"]
    if pos is None:
        return None
    qty = pos["qty"]
    fill = ref_price * (1 - cfg.slippage_sell)
    gross = qty * fill
    fee = gross * cfg.fee_sell
    proceeds = gross - fee
    pnl = proceeds - pos["cost_try"]
    trade = {
        "id": len(state["trades"]) + 1,
        "side": "LONG",
        "entry_time": pos["entry_time"],
        "entry_candle_open_ms": pos["entry_candle_open_ms"],
        "entry_ref_price": pos["entry_ref_price"],
        "entry_price": pos["entry_price"],
        "exit_time": iso(signal_time_ms),
        "exit_candle_open_ms": candle_open_ms,
        "exit_detected_at": iso(detected_ms),
        "exit_ref_price": ref_price,
        "exit_price": fill,
        "qty": qty,
        "entry_fee_try": pos["entry_fee_try"],
        "exit_fee_try": fee,
        "slippage_cost_try": qty * (pos["entry_price"] - pos["entry_ref_price"])
        + qty * (ref_price - fill),
        "cost_try": pos["cost_try"],
        "proceeds_try": proceeds,
        "net_pnl_try": pnl,
        "return_pct": pnl / pos["cost_try"] * 100.0,
        "entry_reason": pos["reason"],
        "exit_reason": reason,
    }
    state["trades"].append(trade)
    state["realized_pnl_try"] += pnl
    state["cash_try"] += proceeds
    state["btc"] = 0.0
    state["position"] = None
    return trade


def mark_equity(state, cfg, price, date_str):
    """Zirve/maksimum drawdown ve günlük değer geçmişini günceller."""
    eq = equity_try(state, cfg, price)
    state["last_price"] = price
    if eq > state["peak_equity_try"]:
        state["peak_equity_try"] = eq
    peak = state["peak_equity_try"]
    dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
    if dd > state["max_drawdown_pct"]:
        state["max_drawdown_pct"] = dd
    hist = state["daily_equity"]
    point = {"date": date_str, "equity_try": eq, "btc_price": price}
    if hist and hist[-1]["date"] == date_str:
        hist[-1] = point
    else:
        hist.append(point)
        del hist[:-800]
    return eq


def stats(state, only_date=None, date_of=None):
    """Kapanan işlem istatistikleri. only_date verilirse o güne (date_of(trade)) göre."""
    trades = state["trades"]
    if only_date is not None:
        trades = [t for t in trades if date_of(t) == only_date]
    pnls = [t["net_pnl_try"] for t in trades]
    win_p = [x for x in pnls if x > 0]
    loss_p = [x for x in pnls if x < 0]
    n = len(trades)
    fees = sum(t["entry_fee_try"] + t["exit_fee_try"] for t in trades)
    slip = sum(t["slippage_cost_try"] for t in trades)
    net = sum(pnls)
    return {
        "closed": n,
        "wins": len(win_p),
        "losses": len(loss_p),
        "win_rate": (len(win_p) / n * 100.0) if n else None,
        "net_pnl_try": net,
        "avg_win_try": (sum(win_p) / len(win_p)) if win_p else None,
        "avg_loss_try": (sum(loss_p) / len(loss_p)) if loss_p else None,
        "profit_factor": (sum(win_p) / -sum(loss_p)) if loss_p else None,
        "fees_try": fees,
        "slippage_try": slip,
        "gross_pnl_try": net + fees + slip,  # maliyetsiz (komisyon ve slipaj yokken) sonuç
    }


def enqueue(state, title, body, now_ms):
    """Bildirimi state içindeki kuyruğa ekler (state kaydından SONRA gönderilir)."""
    state["outbox"].append(
        {"id": state["next_outbox_id"], "created_ms": now_ms, "title": title, "body": body}
    )
    state["next_outbox_id"] += 1
    del state["outbox"][:-50]
