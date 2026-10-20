"""ntfy bildirimleri. Mevcut davranış korunur: aynı topic (NTFY_TOPIC ortam değişkeni /
GitHub Secret), POST + Title/Priority başlıkları, 3 denemeli gönderim.

Güvenlik: topic hiçbir log/hata metnine yazılmaz (scrub). Gönderim hatası ASLA
istisna fırlatmaz, botun ana akışını durdurmaz.
Not: HTTP başlıkları ASCII olmalı (Türkçe 'ı' Title'da hata verir) -> başlıklar ASCII'ye çevrilir.
"""
import time

import requests

NTFY_URL = "https://ntfy.sh"


class Notifier:
    def __init__(self, topic, session=None, sleep=time.sleep, attempts=3, base_url=NTFY_URL):
        self.topic = (topic or "").strip()
        self.session = session or requests.Session()
        self.sleep = sleep
        self.attempts = attempts
        self.base_url = base_url
        self.log = print

    def scrub(self, text):
        text = str(text)
        if self.topic:
            text = text.replace(self.topic, "***")
        return text

    def send(self, message, title="BTC Paper Bot", priority="high"):
        if not self.topic:
            self.log("NTFY_TOPIC tanımlı değil; bildirim gönderilmedi.")
            return False
        safe_title = title.encode("ascii", "replace").decode("ascii")
        for attempt in range(1, self.attempts + 1):
            try:
                r = self.session.post(
                    f"{self.base_url}/{self.topic}",
                    data=message.encode("utf-8"),
                    headers={"Title": safe_title, "Priority": priority},
                    timeout=15,
                )
                if r.status_code == 200:
                    self.log("Bildirim gönderildi.")
                    return True
                self.log(f"Ntfy cevabı {r.status_code} (deneme {attempt}/{self.attempts})")
            except Exception as e:  # noqa: BLE001 - bildirim hatası botu durdurmamalı
                self.log(self.scrub(
                    f"Bildirim gönderilemedi (deneme {attempt}/{self.attempts}): "
                    f"{type(e).__name__}: {e}"
                ))
            if attempt < self.attempts:
                self.sleep(5)
        self.log(f"Bildirim {self.attempts} denemede de gönderilemedi.")
        return False


class NullNotifier:
    """--dry-run için: hiçbir şey göndermez."""

    topic = ""

    def send(self, message, title="", priority="high"):
        print(f"[DRY-RUN bildirim] {title}\n{message}\n")
        return True
