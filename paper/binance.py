"""Binance Spot GENEL piyasa verisi istemcisi (yalnızca GET, anahtarsız).

Mimari güvence: _get yalnızca ALLOWED_PATHS içindeki uç noktalara GET atar.
İmzalı/emir uç noktaları (ör. /api/v3/order) listede yoktur; çağrılırsa hata verir.
API anahtarı, imza veya POST kodu bu dosyada yoktur.
"""
import math
import time
from collections import namedtuple

import requests

ALLOWED_PATHS = frozenset({"/api/v3/time", "/api/v3/klines", "/api/v3/ticker/price"})

Candle = namedtuple("Candle", "open_ms open high low close close_ms")


class DataError(Exception):
    """Veri alınamadı / geçersiz / bayat. Güvenli biçimde beklenmeli."""


def _num(x):
    v = float(x)
    if not math.isfinite(v) or v <= 0:
        raise ValueError("geçersiz sayı")
    return v


def parse_klines(rows):
    out = []
    try:
        for r in rows:
            o, h, l, c = _num(r[1]), _num(r[2]), _num(r[3]), _num(r[4])
            if h < l or not (l <= c <= h) or not (l <= o <= h):
                raise ValueError("tutarsız OHLC")
            out.append(Candle(int(r[0]), o, h, l, c, int(r[6])))
    except (ValueError, TypeError, IndexError) as e:
        raise DataError(f"Geçersiz mum verisi: {e}")
    return out


def closed_tail(candles, server_ms, interval_ms, stale_s, min_candles):
    """Kapanmış, tekrarsız, ardışık (boşluksuz) son mum dizisini döndürür."""
    closed = [c for c in candles if c.close_ms < server_ms]  # oluşmakta olan mum atılır
    if not closed:
        raise DataError("Kapanmış mum yok")
    for a, b in zip(closed, closed[1:]):
        if b.open_ms <= a.open_ms:
            raise DataError("Mumlar sıralı değil veya tekrar ediyor")
    tail = [closed[-1]]
    for c in reversed(closed[:-1]):
        if tail[0].open_ms - c.open_ms != interval_ms:
            break
        tail.insert(0, c)
    if len(tail) < min_candles:
        raise DataError(f"Yeterli ardışık veri yok ({len(tail)}/{min_candles})")
    age_s = (server_ms - (tail[-1].open_ms + interval_ms)) / 1000.0
    if age_s > stale_s:
        raise DataError(f"Veri bayat: son kapanmış mum {age_s:.0f} sn önce")
    return tail


class BinanceClient:
    def __init__(self, base_url="https://api.binance.com", session=None,
                 sleep=time.sleep, timeout=(5, 15), max_attempts=3):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        if session is None:
            self.session.headers["User-Agent"] = "btc-paper-bot/1.0"
        self.sleep = sleep
        self.timeout = timeout
        self.max_attempts = max_attempts

    def _get(self, path, params=None):
        if path not in ALLOWED_PATHS:
            raise ValueError(f"İzin verilmeyen uç nokta: {path}")
        url = self.base_url + path
        last = "bilinmeyen hata"
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"bağlantı hatası ({type(e).__name__})"
                self.sleep(2 * attempt)
                continue
            code = r.status_code
            if code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise DataError("Binance geçersiz JSON döndürdü")
            if code in (403, 451):
                raise DataError(
                    f"Binance erişimi reddedildi (HTTP {code}); bölge/IP engeli olabilir"
                )
            if code == 418:
                raise DataError("Binance IP'yi geçici engelledi (HTTP 418)")
            if code == 429:
                try:
                    wait = min(int(r.headers.get("Retry-After", 5 * attempt)), 30)
                except (ValueError, TypeError):
                    wait = 5 * attempt
                last = "hız sınırı (HTTP 429)"
                self.sleep(wait)
                continue
            if 500 <= code < 600:
                last = f"sunucu hatası (HTTP {code})"
                self.sleep(2 * attempt)
                continue
            raise DataError(f"Beklenmeyen HTTP {code}")
        raise DataError(f"{self.max_attempts} denemede başarısız: {last}")

    def server_time_ms(self):
        data = self._get("/api/v3/time")
        try:
            return int(data["serverTime"])
        except (KeyError, TypeError, ValueError):
            raise DataError("Geçersiz sunucu zamanı")

    def price(self, symbol):
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        try:
            return _num(data["price"])
        except (KeyError, TypeError, ValueError):
            raise DataError(f"Geçersiz fiyat ({symbol})")

    def closed_candles(self, cfg):
        """(kapanmış ardışık mumlar, sunucu zamanı ms) döndürür."""
        server_ms = self.server_time_ms()
        rows = self._get(
            "/api/v3/klines",
            {"symbol": cfg.symbol, "interval": cfg.interval, "limit": cfg.klines_limit},
        )
        if not isinstance(rows, list):
            raise DataError("Geçersiz mum cevabı")
        candles = parse_klines(rows)
        tail = closed_tail(candles, server_ms, cfg.interval_ms,
                           cfg.stale_data_s, cfg.min_candles)
        return tail, server_ms
