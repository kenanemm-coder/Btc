"""Veri çekme, bütünlük kontrolü ve parmak izi (sha256)."""
import hashlib
from datetime import datetime, timezone

import numpy as np

from research.data import fetch_klines

from .strategies import BAR_MS

TF_INTERVAL = {"5m": "5m", "1h": "1h", "4h": "4h"}


def to_ms(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_tf(base_url, symbol, tf, start_ms, end_ms, client=None, log=print):
    om, o, h, l, c = fetch_klines(base_url, symbol, TF_INTERVAL[tf], BAR_MS[tf], start_ms, end_ms,
                                  client=client, log=log)
    return {"open_ms": om, "open": o, "high": h, "low": l, "close": c}


def cut(d, end_ms):
    """open_ms < end_ms olan mumlar."""
    k = int(np.searchsorted(d["open_ms"], end_ms, side="left"))
    return {key: v[:k] for key, v in d.items()}


def fingerprint(d, end_ms=None):
    dd = cut(d, end_ms) if end_ms is not None else d
    h = hashlib.sha256()
    for key in ("open_ms", "open", "high", "low", "close"):
        h.update(np.ascontiguousarray(dd[key]).tobytes())
    return h.hexdigest()


def integrity(d, tf):
    """Eksik mum, yinelenen/sırasız zaman damgası, hatalı OHLC, ızgara ve UTC uyumu."""
    bar = BAR_MS[tf]
    om = d["open_ms"]
    steps = np.diff(om)
    gaps = steps[steps != bar]
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    bad_ohlc = int(np.sum((h < l) | (c > h) | (c < l) | (o > h) | (o < l) | (o <= 0) | (c <= 0)))
    missing = int(np.sum(np.maximum(gaps // bar - 1, 0))) if len(gaps) else 0
    return {
        "tf": tf,
        "bars": int(len(om)),
        "first": datetime.fromtimestamp(om[0] / 1000, tz=timezone.utc).isoformat(),
        "last": datetime.fromtimestamp(om[-1] / 1000, tz=timezone.utc).isoformat(),
        "duplicates": int(np.sum(steps == 0)),
        "unsorted": int(np.sum(steps < 0)),
        "off_grid": int(np.sum(om % bar != 0)),
        "bad_ohlc": bad_ohlc,
        "gap_count": int(len(gaps)),
        "missing_bars": missing,
        "missing_pct": float(missing / (len(om) + missing) * 100.0),
        "longest_gap_bars": int(gaps.max() // bar - 1) if len(gaps) else 0,
    }


def integrity_ok(rep):
    return rep["duplicates"] == 0 and rep["unsorted"] == 0 and rep["off_grid"] == 0 and rep["bad_ohlc"] == 0
