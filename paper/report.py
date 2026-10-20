"""Bildirim ve günlük rapor metinleri (Türkçe).

Performans TRY bazlıdır (strateji sonucu). USDT karşılıkları BAŞLANGIÇ kuruyla
gösterilir ki USDT/TRY kur değişimi strateji performansına karışmasın; güncel kurla
değer ve "kur etkisi" ayrıca verilir.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import MODE
from . import portfolio as pf


def money(x, cur="₺"):
    return f"{cur}{x:,.2f}"


def signed(x, cur="₺"):
    return f"{'+' if x >= 0 else '-'}{cur}{abs(x):,.2f}"


def to_usdt(state, try_amount):
    return try_amount / state["initial"]["usdt_try_rate"]


def trade_local_date(trade, tz):
    dt = datetime.fromisoformat(trade["exit_time"]).astimezone(ZoneInfo(tz))
    return dt.date().isoformat()


def init_message(state, cfg):
    ini = state["initial"]
    return (
        f"🟢 PAPER (sanal) mod aktif — gerçek emir GÖNDERİLMEZ.\n"
        f"Parite: {cfg.symbol} (Binance Spot) | Mum: {cfg.interval}\n"
        f"Başlangıç: {ini['usdt']:,.2f} USDT × {ini['usdt_try_rate']:,.4f} "
        f"= {money(ini['try'])}\n"
        f"Varsayımlar: komisyon alış %{cfg.fee_buy*100:.2f} / satış %{cfg.fee_sell*100:.2f}, "
        f"slipaj alış %{cfg.slippage_buy*100:.2f} / satış %{cfg.slippage_sell*100:.2f}"
    )


def buy_message(state, cfg, pos):
    return (
        f"🟢 SANAL ALIŞ (PAPER) {cfg.symbol}\n"
        f"Sinyal: {pos['reason']}\n"
        f"Referans: {money(pos['entry_ref_price'])} | Giriş (slipaj dahil): {money(pos['entry_price'])}\n"
        f"Miktar: {pos['qty']:.8f} BTC | Komisyon: {money(pos['entry_fee_try'])}\n"
        f"Harcanan: {money(pos['cost_try'])}"
    )


def close_message(state, cfg, trade):
    pnl = trade["net_pnl_try"]
    icon = "✅" if pnl > 0 else "🔻"
    return (
        f"{icon} SANAL POZİSYON KAPANDI (PAPER) {cfg.symbol}\n"
        f"Neden: {trade['exit_reason']}\n"
        f"Giriş: {money(trade['entry_price'])} → Çıkış: {money(trade['exit_price'])} (slipaj dahil)\n"
        f"Net K/Z: {signed(pnl)} ({signed(to_usdt(state, pnl), '')} USDT) | "
        f"Getiri: {trade['return_pct']:+.2f}%\n"
        f"Komisyon: {money(trade['entry_fee_try'] + trade['exit_fee_try'])}"
    )


def daily_report(state, cfg, report_date, usdt_try_now, missed_days=0):
    price = state["last_price"]
    ini = state["initial"]
    eq = pf.equity_try(state, cfg, price)
    total = eq - ini["try"]
    total_pct = total / ini["try"] * 100.0
    realized = state["realized_pnl_try"]
    unreal = pf.unrealized_pnl_try(state, cfg, price)
    st_all = pf.stats(state)
    st_day = pf.stats(state, report_date.isoformat(),
                      lambda t: trade_local_date(t, cfg.tz))

    def wr(s):
        return f"%{s['win_rate']:.0f}" if s["win_rate"] is not None else "-"

    lines = [f"📊 PAPER Günlük Rapor — {report_date.strftime('%d/%m/%Y')} (TSİ)"]
    if missed_days > 0:
        lines.append(f"(⚠️ {missed_days} günlük rapor atlandı; bu rapor güncel durumu gösterir)")
    lines += [
        f"Mod: {MODE} | Parite: {cfg.symbol}",
        f"Son fiyat: {money(price)}",
        f"Toplam portföy: {to_usdt(state, eq):,.2f} USDT (başlangıç kuru) = {money(eq)}",
        f"Toplam net K/Z: {signed(to_usdt(state, total), '')} USDT / {total_pct:+.2f}% (TRY bazlı)",
        f"  Gerçekleşmiş: {signed(realized)} | Gerçekleşmemiş: {signed(unreal)}",
        f"Nakit: {money(state['cash_try'])} | BTC: {state['btc']:.8f}",
    ]
    if state["position"]:
        p = state["position"]
        lines.append(f"Açık pozisyon: giriş {money(p['entry_price'])} ({p['entry_time'][:16]} UTC)")
    else:
        lines.append("Açık pozisyon: yok")
    lines += [
        f"Bugün kapanan: {st_day['closed']} (K {st_day['wins']} / Z {st_day['losses']}), "
        f"net {signed(st_day['net_pnl_try'])}",
        f"Toplam kapanan: {st_all['closed']} (K {st_all['wins']} / Z {st_all['losses']}), "
        f"kazanma oranı {wr(st_all)}",
        f"Maks. drawdown: %{state['max_drawdown_pct']:.2f}",
    ]
    lines.append(f"Kur (USDT/TRY): başlangıç {ini['usdt_try_rate']:,.4f}")
    if usdt_try_now:
        now_val = eq / usdt_try_now
        fx_effect = now_val - to_usdt(state, eq)
        lines.append(
            f"  güncel {usdt_try_now:,.4f} → portföy {now_val:,.2f} USDT; "
            f"kur etkisi {fx_effect:+,.2f} USDT (strateji sonucundan ayrı)"
        )
    return "\n".join(lines)
