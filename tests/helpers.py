"""Test yardımcıları: sahte piyasa verisi, sahte Binance istemcisi, sahte bildirimci."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from paper.binance import Candle, closed_tail
from paper.config import Config
from paper.indicators import compute_row, signal_from_row

IV = 300_000
CFG = Config()


def ms_at(year, month, day, hour, minute=0, tz="Europe/Istanbul"):
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def candles_from_closes(closes, last_close_ms):
    """Kapanışlardan mum listesi; son mum last_close_ms anında kapanır."""
    n = len(closes)
    first_open = last_close_ms - n * IV
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = first_open + i * IV
        out.append(Candle(o, prev, max(prev, c) * 1.0005, min(prev, c) * 0.9995, c, o + IV - 1))
        prev = c
    return out


def base_closes():
    return [100 + 0.5 * i + 0.2 * math.sin(i) for i in range(80)]


def buy_series():
    """80 mum yükseliş + 81. mumda alım sinyali veren düşüş."""
    base = base_closes()
    s = base + [base[-1] - 11.5]
    assert signal_from_row(compute_row(s)) == "buy"
    return s


def sell_series():
    """buy_series + toparlanma + üst bant sinyali veren sıçrama (sonuncu mum SATIŞ)."""
    s = buy_series()
    for _ in range(3):
        s.append(s[-1] + 1.0)
    k = 1.0
    while True:
        t = s + [s[-1] + k]
        if signal_from_row(compute_row(t)) == "sell":
            return t
        k += 0.5
        assert k < 50


class FakeClient:
    """Binance istemcisi yerine geçer. closed_candles gerçek closed_tail kullanır."""

    def __init__(self, closes, last_close_ms, lag_ms=10_000, rate=40.0, price_now=None):
        self.rate = rate
        self.fail = None
        self.set(closes, last_close_ms, lag_ms)
        self.calls = []

    def set(self, closes, last_close_ms, lag_ms=10_000):
        self.all = candles_from_closes(closes, last_close_ms)
        self.server_ms = last_close_ms + lag_ms

    def closed_candles(self, cfg):
        self.calls.append("closed_candles")
        if self.fail:
            raise self.fail
        tail = closed_tail(self.all, self.server_ms, cfg.interval_ms, cfg.stale_data_s, cfg.min_candles)
        return tail, self.server_ms

    def price(self, symbol):
        self.calls.append("price:" + symbol)
        if self.fail:
            raise self.fail
        return self.rate


class FakeNotifier:
    topic = "gizli-topic"

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, message, title="", priority="high"):
        if self.ok:
            self.sent.append((title, message))
        return self.ok

    def scrub(self, t):
        return str(t)
