"""Geçmiş BTC/TRY 5 dakikalık mumları Binance genel verisinden çeker (yalnızca GET, anahtarsız)."""
import time

import numpy as np

from paper.binance import BinanceClient, DataError, parse_klines


def fetch_klines(base_url, symbol="BTCTRY", interval="5m", interval_ms=300_000, start_ms=0,
                 end_ms=None, pause_s=0.08, client=None, log=print):
    """(open_ms, open, high, low, close) numpy dizileri; kapanmamış son mum atılır."""
    client = client or BinanceClient(base_url)
    server_ms = client.server_time_ms()
    end_ms = server_ms if end_ms is None else min(end_ms, server_ms)
    rows, cur, pages = [], start_ms, 0
    while cur < end_ms:
        data = client._get("/api/v3/klines", {"symbol": symbol, "interval": interval,
                                              "startTime": cur, "endTime": end_ms, "limit": 1000})
        if not isinstance(data, list):
            raise DataError("Geçersiz mum cevabı")
        if not data:
            break
        candles = parse_klines(data)
        rows.extend(c for c in candles if c.close_ms < server_ms)
        nxt = candles[-1].open_ms + interval_ms
        if nxt <= cur:
            raise DataError("Sayfalama ilerlemiyor")
        cur = nxt
        pages += 1
        if pages % 50 == 0:
            log(f"  {pages} sayfa, {len(rows)} mum")
        time.sleep(pause_s)
    if not rows:
        raise DataError("Hiç mum alınamadı")
    arr = np.array([(c.open_ms, c.open, c.high, c.low, c.close) for c in rows], dtype="float64")
    om = arr[:, 0].astype("int64")
    if np.any(np.diff(om) <= 0):
        raise DataError("Mumlar sıralı değil veya tekrar ediyor")
    return om, arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]


def data_quality(open_ms, interval_ms=300_000):
    """Veri kalitesi özeti: kapsam, eksik mum sayısı, en uzun boşluk."""
    steps = np.diff(open_ms)
    gaps = steps[steps != interval_ms]
    missing = int(np.sum(gaps // interval_ms - 1)) if len(gaps) else 0
    return {
        "candles": int(len(open_ms)),
        "first_ms": int(open_ms[0]),
        "last_ms": int(open_ms[-1]),
        "gap_count": int(len(gaps)),
        "missing_candles": missing,
        "longest_gap_candles": int(gaps.max() // interval_ms - 1) if len(gaps) else 0,
        "missing_pct": missing / (len(open_ms) + missing) * 100.0,
    }
