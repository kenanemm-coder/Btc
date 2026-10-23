"""Sentetik veriyle denetim hattının kendi kendine testi (ağ yok)."""
import json, os, sys, tempfile
import numpy as np
from paper.binance import BinanceClient
from strategy_lab import run as R, spec
from . import audit

T_NOW = 1792728000000 + 20 * 60_000
START = R.to_ms("2020-10-01")
rng = np.random.default_rng(7)
N5 = (T_NOW - START) // 300_000
ret = rng.normal(2e-6, 1.5e-3, N5)
cl = 100_000 * np.exp(np.cumsum(ret))
op = np.concatenate([[100_000.0], cl[:-1]])
spread = np.abs(rng.normal(0, 1e-3, N5))
hi = np.maximum(op, cl) * (1 + spread); lo = np.minimum(op, cl) * (1 - spread)
om5 = START + np.arange(N5) * 300_000

def agg(k, ms):
    m = N5 // k * k
    o = op[:m].reshape(-1, k)[:, 0]; c = cl[:m].reshape(-1, k)[:, -1]
    h = hi[:m].reshape(-1, k).max(1); l = lo[:m].reshape(-1, k).min(1)
    t = om5[:m].reshape(-1, k)[:, 0]
    return t, o, h, l, c

SER = {"5m": (agg(1, 300_000), 300_000), "1h": (agg(12, 3_600_000), 3_600_000), "4h": (agg(48, 14_400_000), 14_400_000)}

class Fake(audit.SpyClient):
    def __init__(self, *a, **k):
        BinanceClient.__init__(self, "http://x"); self.kline_end_times = []
    def _get(self, path, params=None):
        if path == "/api/v3/time":
            return {"serverTime": T_NOW}
        self.kline_end_times.append(int(params["endTime"]))
        (t, o, h, l, c), ms = SER[{300000: "5m", 3600000: "1h", 14400000: "4h"}[0] if False else None] if False else (None, None)
        return None

def make(interval):
    (t, o, h, l, c), ms = SER[interval]
    return t, o, h, l, c, ms

class Fake2(Fake):
    def _get(self, path, params=None):
        if path == "/api/v3/time":
            return {"serverTime": T_NOW}
        self.kline_end_times.append(int(params["endTime"]))
        t, o, h, l, c, ms = make(params["interval"])
        a = np.searchsorted(t, params["startTime"], "left"); b = np.searchsorted(t, params["endTime"], "right")
        b = min(b, a + params.get("limit", 1000))
        return [[int(t[i]), str(o[i]), str(h[i]), str(l[i]), str(c[i]), "1", int(t[i] + ms - 1)] for i in range(a, b)]

def main():
    audit.SpyClient = Fake2
    R.BinanceClient = Fake2
    out = tempfile.mkdtemp()
    R.stage_select(out, "http://x", log=lambda *_: None, client=Fake2())
    R.stage_final(out, "http://x", None, log=lambda *_: None, client=Fake2())
    audit.main(["--sel", out + "/selection.json", "--res", out + "/results_final.json", "--out", out + "/aud", "--n-sims", "20", "--base-sha", "HEAD"])
    print(open(out + "/aud/audit.json").read()[:1500])

if __name__ == "__main__":
    main()
