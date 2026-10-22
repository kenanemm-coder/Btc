"""Bekçi: bot beklenenden uzun süredir başarılı çalışmadıysa ntfy ile uyarır.

Ayrı bir workflow olarak saatte bir çalışır (aynı GitHub Actions altyapısında; GitHub'ın
tamamı kesilirse bekçi de çalışamaz — bu sınır PAPER_TRADING.md'de yazılıdır).
Durumsuzdur: tekrar uyarı, geçen süreye göre hesaplanır (varsayılan 6 saatte bir).
"""
import argparse
import os
import sys
import time

from . import health
from .config import ConfigError, from_env
from .notify import NullNotifier, Notifier
from .state import StateError, StateMissing, load_state

TITLE = "Paper Bot BEKCI ALARM"


def decide(state_path, cfg, now_ms, interval_s=3600, repeat_h=6.0):
    """(durum, mesaj, uyar_mı). durum: ok | stale | missing | corrupt."""
    repeat_s = repeat_h * 3600
    max_age_s = cfg.watchdog_max_age_min * 60
    bucket_ok = (now_ms // 1000 // interval_s) % max(1, int(repeat_s // interval_s)) == 0
    try:
        state = load_state(state_path, cfg)
    except StateMissing:
        return "missing", "🚨 Bot state dosyası bulunamıyor (data dalı/paper_state.json). Bot çalışmıyor olabilir.", bucket_ok
    except StateError as e:
        return "corrupt", f"🚨 Bot state dosyası okunamıyor: {e}", bucket_ok
    good = health.last_good_ms(state, cfg.interval_ms)
    if good is None:
        return "missing", "🚨 Botun son çalışma zamanı bilinmiyor.", bucket_ok
    age_s = (now_ms - good) / 1000.0
    if age_s < max_age_s:
        return "ok", f"Bot sağlıklı: son başarılı çalışma {age_s / 60:.0f} dk önce.", False
    h = state.get("health") or {}
    extra = f" Son hata: {h['last_error']}" if h.get("last_error") else ""
    msg = (f"🚨 Bot {age_s / 3600:.1f} saattir başarılı çalışmadı "
           f"(sınır {cfg.watchdog_max_age_min} dk).{extra}")
    over = age_s - max_age_s
    return "stale", msg, (over % repeat_s) < interval_s


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper bot bekçisi")
    ap.add_argument("--state", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = from_env()
    except ConfigError as e:
        print(f"Ayar hatası: {e}")
        return 2
    topic = os.environ.get("NTFY_TOPIC", "")
    notifier = NullNotifier() if args.dry_run else Notifier(topic)
    status, msg, alert = decide(args.state, cfg, int(time.time() * 1000))
    print(f"[{status}] {msg}")
    if alert and not args.dry_run:
        notifier.send(msg, title=TITLE, priority="urgent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
