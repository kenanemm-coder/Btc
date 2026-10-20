"""Paper trading çalıştırıcı: tek çalıştırma (GitHub Actions) veya sürekli döngü (--loop).

Akış (run_once):
  1. State yükle (yoksa yalnızca --init ile oluştur; bozuksa DUR + bildir).
  2. Binance'ten KAPANMIŞ mumları al ve doğrula (hata/bayat veri -> güvenle bekle).
  3. State'teki son işlenen mumdan sonraki her kapanmış mumu sırayla işle.
  4. Günlük rapor zamanı geldiyse kuyruğa ekle.
  5. State'i atomik kaydet, SONRA bildirim kuyruğunu gönder (çökmede mükerrer işlem olmaz).
Gerçek emir gönderen hiçbir kod yoktur.
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import MODE
from . import portfolio as pf
from . import report
from .binance import BinanceClient, DataError
from .config import ConfigError, from_env
from .indicators import compute_row, signal_from_row
from .notify import NullNotifier, Notifier
from .state import FileLock, LockError, StateError, StateMissing, load_state, save_state

BUY_REASON = "Alt banda temas + RSI<48 + SMA50 üstü"
SELL_REASON = "Üst banda temas"
OUTBOX_MAX_AGE_MS = 48 * 3600 * 1000


def _wall_ms():
    return int(time.time() * 1000)


def due_report_date(now_local, report_hour):
    d = now_local.date()
    return d if now_local.hour >= report_hour else d - timedelta(days=1)


def error_notice(notifier, throttle_path, key, text, wall_ms, interval_h):
    """Kritik hata bildirimi; aynı anahtar için en fazla interval_h saatte bir."""
    try:
        with open(throttle_path, "r", encoding="utf-8") as f:
            seen = json.load(f)
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    last = seen.get(key)
    if isinstance(last, int) and wall_ms - last < interval_h * 3600 * 1000:
        return False
    seen[key] = wall_ms
    try:
        d = os.path.dirname(os.path.abspath(throttle_path)) or "."
        os.makedirs(d, exist_ok=True)
        tmp = throttle_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seen, f)
        os.replace(tmp, throttle_path)
    except OSError:
        pass
    return notifier.send(text, title="Paper Bot Hatasi")


def flush_outbox(state, notifier, now_ms):
    """Sırayla gönder; başarısız olan ve sonrakiler bir sonraki çalıştırmaya kalır."""
    state["outbox"] = [m for m in state["outbox"] if now_ms - m["created_ms"] <= OUTBOX_MAX_AGE_MS]
    sent = 0
    while state["outbox"]:
        m = state["outbox"][0]
        if not notifier.send(m["body"], title=m["title"]):
            break
        state["outbox"].pop(0)
        sent += 1
    return sent


def process_candles(state, cfg, candles, server_ms, tz, notes):
    """Yeni kapanmış mumları sırayla işler. Dönüş: (yeni mum sayısı, işlemler)."""
    closes = [c.close for c in candles]
    new_count = 0
    events = []
    for i, c in enumerate(candles):
        if c.open_ms <= state["last_candle_open_ms"]:
            continue  # aynı mum ikinci kez işlenmez
        new_count += 1
        sig = signal_from_row(compute_row(closes[max(0, i - 59): i + 1]))
        close_ms = c.open_ms + cfg.interval_ms
        fresh = (server_ms - close_ms) <= cfg.max_signal_age_s * 1000
        if sig == "buy":
            if state["position"] is not None:
                notes.append(f"{c.open_ms}: ALIM sinyali ama pozisyon açık — yok sayıldı")
            elif not fresh:
                state["skipped_stale_signals"] += 1
                notes.append(f"{c.open_ms}: ALIM sinyali bayat — işlem açılmadı")
            else:
                pos = pf.open_buy(state, cfg, c.close, c.open_ms, close_ms, server_ms, BUY_REASON)
                if pos:
                    pf.enqueue(state, "Paper Alis", report.buy_message(state, cfg, pos), server_ms)
                    events.append(("buy", pos))
        elif sig == "sell":
            if state["position"] is None:
                notes.append(f"{c.open_ms}: SATIŞ sinyali ama pozisyon yok — short açılmaz")
            elif not fresh:
                state["skipped_stale_signals"] += 1
                notes.append(f"{c.open_ms}: SATIŞ sinyali bayat — işlem yapılmadı")
            else:
                tr = pf.close_position(state, cfg, c.close, c.open_ms, close_ms, server_ms, SELL_REASON)
                if tr:
                    pf.enqueue(state, "Paper Satis", report.close_message(state, cfg, tr), server_ms)
                    events.append(("sell", tr))
        local_date = datetime.fromtimestamp(close_ms / 1000.0, tz).date().isoformat()
        pf.mark_equity(state, cfg, c.close, local_date)
        state["last_candle_open_ms"] = c.open_ms
    return new_count, events


def run_once(cfg, client, notifier, state_path, init=False, dry_run=False, wall_ms=None):
    tz = ZoneInfo(cfg.tz)
    wall_ms = _wall_ms() if wall_ms is None else wall_ms
    throttle = os.path.join(os.path.dirname(os.path.abspath(state_path)), "error_notice.json")
    result = {"status": "ok", "new_candles": 0, "events": [], "notes": [],
              "sent": 0, "initialized": False, "reported": False}

    def fail(status, key, text):
        result["status"] = status
        print(f"[{status}] {text}")
        if not dry_run:
            error_notice(notifier, throttle, key, f"⚠️ {text}", wall_ms, cfg.error_notify_interval_h)
        return result

    # 1) State
    state = None
    try:
        if os.path.exists(state_path):
            state = load_state(state_path, cfg)
        elif not init:
            raise StateMissing(
                "State dosyası bulunamadı. Portföy otomatik sıfırlanmaz; ilk kurulum için --init kullanın."
            )
    except StateError as e:
        return fail("state_error", "state", f"State hatası, bot DURDU: {e}")

    # 2) Veri
    try:
        candles, server_ms = client.closed_candles(cfg)
        fx_rate = client.price(cfg.fx_symbol) if state is None else None
    except DataError as e:
        return fail("data_error", "data", f"Binance veri hatası, işlem yapılmadı (bekleniyor): {e}")

    now_local = datetime.fromtimestamp(server_ms / 1000.0, tz)
    due = due_report_date(now_local, cfg.report_hour)

    # 3) İlk kurulum
    if state is None:
        last = candles[-1]
        state = pf.new_state(cfg, fx_rate, server_ms, last.open_ms, last.close, due.isoformat())
        pf.mark_equity(state, cfg, last.close, now_local.date().isoformat())
        pf.enqueue(state, "Paper Bot Basladi", report.init_message(state, cfg), server_ms)
        result["initialized"] = True
    else:
        n, events = process_candles(state, cfg, candles, server_ms, tz, result["notes"])
        result["new_candles"] = n
        result["events"] = events

    # 4) Günlük rapor
    last_rep = date.fromisoformat(state["last_report_date"])
    if last_rep < due:
        try:
            rate_now = client.price(cfg.fx_symbol)
        except DataError:
            rate_now = None
        body = report.daily_report(state, cfg, due, rate_now, missed_days=(due - last_rep).days - 1)
        pf.enqueue(state, "Paper Gunluk Rapor", body, server_ms)
        state["last_report_date"] = due.isoformat()
        result["reported"] = True

    if dry_run:
        for m in state["outbox"]:
            print(f"[DRY-RUN] {m['title']}\n{m['body']}\n")
        print(f"[DRY-RUN] yeni mum: {result['new_candles']}, notlar: {result['notes']}")
        return result

    # 5) Kaydet, sonra bildir
    save_state(state_path, state)
    result["sent"] = flush_outbox(state, notifier, server_ms)
    if result["sent"]:
        save_state(state_path, state)
    print(f"Tamam: yeni mum {result['new_candles']}, işlem {len(result['events'])}, "
          f"bildirim {result['sent']}, bekleyen {len(state['outbox'])}")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"BTC/TRY {MODE} (sanal) işlem botu")
    ap.add_argument("--state", default=os.path.join("paper_data", "paper_state.json"))
    ap.add_argument("--init", action="store_true", help="State yoksa yeni sanal portföy oluştur")
    ap.add_argument("--loop", action="store_true", help="Sürekli çalış (yerel/VPS)")
    ap.add_argument("--dry-run", action="store_true", help="Bildirim ve state yazma yok; sadece göster")
    args = ap.parse_args(argv)

    try:
        cfg = from_env()
    except ConfigError as e:
        print(f"Ayar hatası: {e}")
        return 2

    topic = os.environ.get("NTFY_TOPIC", "")
    notifier = NullNotifier() if args.dry_run else Notifier(topic)
    if not topic and not args.dry_run:
        print("UYARI: NTFY_TOPIC tanımlı değil; bildirim gönderilmeyecek.")
    client = BinanceClient(cfg.base_url)

    try:
        with FileLock(args.state + ".lock"):
            if not args.loop:
                res = run_once(cfg, client, notifier, args.state, args.init, args.dry_run)
                return 2 if res["status"] == "state_error" else 0
            init = args.init
            while True:
                try:
                    res = run_once(cfg, client, notifier, args.state, init, args.dry_run)
                    if res["status"] == "state_error":
                        return 2
                    init = False
                except Exception as e:  # noqa: BLE001 - döngü geçici hatada çökmesin
                    msg = f"Beklenmeyen hata: {type(e).__name__}"
                    print(notifier.scrub(msg) if hasattr(notifier, "scrub") else msg)
                now_ms = _wall_ms()
                nxt = (now_ms // cfg.interval_ms + 1) * cfg.interval_ms + 5000
                time.sleep(max(1, (nxt - now_ms) / 1000.0))
    except LockError as e:
        print(str(e))
        return 3
    except KeyboardInterrupt:
        print("Durduruldu.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
