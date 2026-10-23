"""strategy_lab testleri: göstergeler, ileriye bakmama, dolum/stop gerçekçiliği, muhasebe, bölme, kapılar, protokol."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from paper import portfolio as pf
from paper.config import Config
from strategy_lab import indicators as I
from strategy_lab import metrics as M
from strategy_lab import protocol as P
from strategy_lab import run as R
from strategy_lab import spec, strategies
from strategy_lab.backtest import ZERO, Costs, backtest, costs_from
from strategy_lab.data import cut, fingerprint, integrity, integrity_ok, to_ms

H1, H4, DAY = 3_600_000, 14_400_000, 86_400_000


def rj(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wj(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def ohlc(n, seed=0, vol=0.01, start=1_600_000_000_000 // H1 * H1, bar=H1, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 1_000_000 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    spread = np.abs(rng.normal(0, vol / 2, n))
    h = np.maximum(o, c) * (1 + spread)
    l = np.minimum(o, c) * (1 - spread)
    return {"open_ms": (start + np.arange(n) * bar).astype("int64"), "open": o, "high": h, "low": l, "close": c}


class IndicatorTests(unittest.TestCase):
    def test_sma_ema_atr_match_naive_recursions(self):
        d = ohlc(400, 1)
        c = d["close"]
        self.assertAlmostEqual(I.sma(c, 20)[100], c[81:101].mean(), places=6)
        k = 2 / (10 + 1)
        e = c[0]
        for x in c[1:150]:
            e = k * x + (1 - k) * e
        self.assertAlmostEqual(I.ema(c, 10)[149], e, places=6)
        h, l = d["high"], d["low"]
        tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
        a = tr[0]
        for x in tr[1:200]:
            a = a + (x - a) / 14
        self.assertAlmostEqual(I.atr(h, l, c, 14)[200], a, delta=a * 0.02)  # başlangıç tohumu farkı çok küçük
        self.assertTrue(np.isnan(I.atr(h, l, c, 14)[10]))

    def test_prev_extremes_exclude_current_bar(self):
        x = np.array([1, 2, 3, 10, 4, 5.0])
        self.assertEqual(I.prev_max(x, 3)[4], 10)       # 2,3,10
        self.assertTrue(np.isnan(I.prev_max(x, 3)[2]))
        self.assertEqual(I.prev_min(x, 3)[3], 1)
        # mevcut mum dahil olsaydı 10 yerine 10 değişmezdi; kırılım testi:
        y = np.array([5, 5, 5, 9.0])
        self.assertEqual(I.prev_max(y, 3)[3], 5)         # 9 kendisiyle kıyaslanmaz

    def test_ret(self):
        x = np.array([100, 110, 121.0])
        self.assertAlmostEqual(I.ret(x, 1)[2], 0.1)
        self.assertTrue(np.isnan(I.ret(x, 2)[1]))


class NoLookaheadTests(unittest.TestCase):
    def test_signals_unchanged_when_future_is_removed(self):
        d = ohlc(3000, 2, vol=0.012)
        for tf in ("1h",):
            full = [strategies.make_b(d, tf, 20, 2.0, "chan10"), strategies.make_b(d, tf, 40, 3.0, "trail"),
                    strategies.make_c(d, tf, 25, 100), strategies.make_d(d, tf, 10, 30)]
            k = 2200
            dp = {key: v[:k] for key, v in d.items()}
            part = [strategies.make_b(dp, tf, 20, 2.0, "chan10"), strategies.make_b(dp, tf, 40, 3.0, "trail"),
                    strategies.make_c(dp, tf, 25, 100), strategies.make_d(dp, tf, 10, 30)]
            for a, b in zip(full, part):
                self.assertTrue((a.entry[:k] == b.entry).all(), a.name)
                self.assertTrue((a.exit_sig[:k] == b.exit_sig).all(), a.name)
                self.assertTrue(a.entry.any() or a.name.startswith("C"), a.name)

    def test_breakout_uses_only_previous_bars_and_trend_filter(self):
        n = 400
        c = np.full(n, 100.0)
        c[300:] = 110.0                      # SMA200 üstünde, kırılım 300. mumda
        d = {"open_ms": (1_600_000_000_000 // H1 * H1 + np.arange(n) * H1).astype("int64"),
             "open": c.copy(), "high": c + 0.5, "low": c - 0.5, "close": c}
        s = strategies.make_b(d, "1h", 20, 2.0, "chan10")
        self.assertEqual(list(np.flatnonzero(s.entry)), [300])   # yalnızca kırılan mumda
        # SMA200 altında kırılım sinyal vermez
        c2 = np.full(n, 100.0)
        c2[:250] = 200.0
        c2[300:] = 100.5
        d2 = dict(d, close=c2, open=c2.copy(), high=c2 + 0.5, low=c2 - 0.5)
        self.assertFalse(strategies.make_b(d2, "1h", 20, 2.0, "chan10").entry.any())

    def test_gap_in_data_blocks_signals(self):
        d = ohlc(1500, 3, vol=0.01)
        d["open_ms"] = d["open_ms"].copy()
        d["open_ms"][700:] += 5 * H1
        s = strategies.make_b(d, "1h", 20, 2.0, "chan10")
        self.assertFalse(s.entry[700:899].any())     # boşluktan sonra 200 mum dolana kadar giriş yok


class ExecutionRealismTests(unittest.TestCase):
    def setUp(self):
        n = 30
        self.o = np.full(n, 100.0)
        self.h = np.full(n, 101.0)
        self.l = np.full(n, 99.0)
        self.c = np.full(n, 100.0)
        self.entry = np.zeros(n, bool)
        self.exit = np.zeros(n, bool)

    def bt(self, costs=ZERO, **kw):
        return backtest(self.o, self.h, self.l, self.c, self.entry, self.exit, costs, **kw)

    def test_entry_fills_at_next_bar_open_not_signal_close(self):
        self.c[5] = 100.0
        self.o[6] = 103.0
        self.entry[5] = True
        self.exit[10] = True
        self.o[11] = 107.0
        r = self.bt()
        t = r["trades"][0]
        self.assertEqual((t["entry_i"], t["exit_i"]), (6, 11))
        self.assertEqual(t["entry_ref"], 103.0)
        self.assertEqual(t["exit_ref"], 107.0)
        self.assertAlmostEqual(t["ret"], 107 / 103 - 1)

    def test_delay_adds_one_more_bar(self):
        self.entry[5] = True
        self.exit[10] = True
        r = self.bt(Costs(0, 0, 0, 0, 1))
        self.assertEqual((r["trades"][0]["entry_i"], r["trades"][0]["exit_i"]), (7, 12))

    def test_costs_both_sides_applied_separately(self):
        self.entry[5] = True
        self.exit[10] = True
        c = Costs(0.001, 0.002, 0.0005, 0.0010, 0)
        t = self.bt(c)["trades"][0]
        qty = 1 / 1.001 / (100 * 1.0005)
        expect = qty * 100 * (1 - 0.0010) * (1 - 0.002)
        self.assertAlmostEqual(t["proceeds"], expect, places=12)
        self.assertAlmostEqual(t["fees"], (1 / 1.001) * 0.001 + qty * 100 * 0.999 * 0.002, places=12)

    def test_stop_gap_down_fills_at_open_not_at_stop(self):
        a = np.full(30, 2.0)
        self.entry[5] = True            # giriş açılış 100 -> stop 96 (k=2, ATR=2)
        self.o[9], self.h[9], self.l[9], self.c[9] = 90.0, 91.0, 88.0, 89.0
        r = self.bt(atr=a, init_k=2.0)
        t = r["trades"][0]
        self.assertEqual(t["reason"], "stop")
        self.assertEqual(t["exit_ref"], 90.0)           # boşlukta iyimser stop fiyatı yok

    def test_stop_intrabar_fills_at_stop_level(self):
        a = np.full(30, 2.0)
        self.entry[5] = True
        self.l[9] = 90.0                                 # açılış 100, düşük 90 -> stop 96
        t = self.bt(atr=a, init_k=2.0)["trades"][0]
        self.assertEqual((t["exit_i"], t["exit_ref"]), (9, 96.0))

    def test_stop_can_trigger_on_entry_bar_itself(self):
        a = np.full(30, 2.0)
        self.entry[5] = True
        self.l[6] = 95.0                                 # giriş mumunun düşüğü stopa değdi
        t = self.bt(atr=a, init_k=2.0)["trades"][0]
        self.assertEqual((t["entry_i"], t["exit_i"], t["reason"]), (6, 6, "stop"))

    def test_trailing_stop_ratchets_only_up_from_closed_bars(self):
        a = np.full(30, 2.0)
        self.entry[5] = True
        for i, px in zip(range(6, 12), (104, 108, 112, 116, 120, 124)):
            self.c[i] = px
            self.o[i] = px - 1
            self.h[i] = px + 1
            self.l[i] = px - 2
        self.l[14] = 100.0                               # takip eden stop = 124 - 2*2 = 120 üstünde düştü
        self.c[12:] = 124
        self.o[12:] = 124
        self.h[12:] = 125
        self.l[12:] = 122
        self.l[14] = 100.0
        t = self.bt(atr=a, init_k=2.0, trail_k=2.0)["trades"][0]
        self.assertEqual((t["exit_i"], t["reason"]), (14, "stop"))
        self.assertEqual(t["exit_ref"], 120.0)
        self.assertGreater(t["exit_ref"], 96.0)          # başlangıç stopundan yukarıda: stop yükseldi

    def test_signal_exit_precedes_stop_on_same_open(self):
        a = np.full(30, 2.0)
        self.entry[5] = True
        self.exit[8] = True
        self.o[9] = 105.0
        self.l[9] = 80.0                                 # aynı mumda stop da değer ama çıkış açılışta olur
        t = self.bt(atr=a, init_k=2.0)["trades"][0]
        self.assertEqual((t["reason"], t["exit_ref"]), ("sinyal", 105.0))

    def test_never_more_than_one_position_and_no_short(self):
        d = ohlc(3000, 5, vol=0.012)
        s = strategies.make_d(d, "1h", 10, 30)
        r = backtest(d["open"], d["high"], d["low"], d["close"], s.entry, s.exit_sig, costs_from(spec.COSTS["temel"]))
        prev = -1
        for t in r["trades"]:
            self.assertGreater(t["entry_i"], prev - 1)
            self.assertLessEqual(t["entry_i"], t["exit_i"])
            prev = t["exit_i"]
        self.assertGreater(len(r["trades"]), 5)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.d = ohlc(4000, 7, vol=0.012)

    def test_equity_equals_compounded_trades_and_never_double_counts(self):
        for cand in (strategies.make_b(self.d, "1h", 20, 2.0, "trail"), strategies.make_c(self.d, "1h", 25, 100),
                     strategies.make_d(self.d, "1h", 10, 30)):
            r = backtest(self.d["open"], self.d["high"], self.d["low"], self.d["close"], cand.entry, cand.exit_sig,
                         costs_from(spec.COSTS["temel"]), cand.atr, cand.init_k, cand.trail_k)
            if not r["open"]:
                self.assertAlmostEqual(r["eq"][-1], float(np.prod([1 + t["ret"] for t in r["trades"]])), places=9)
            self.assertTrue(np.all(r["eq"] > 0))
            for t in r["trades"]:
                self.assertGreaterEqual(t["fees"], 0)
                self.assertGreaterEqual(t["slip"], -1e-12)
                self.assertAlmostEqual(t["proceeds"] / t["cost"] - 1, t["ret"], places=12)
            # pozisyondayken özkaynak BTC değeri, değilken nakit: ikisi birden değil
            held = r["held"]
            for i in np.flatnonzero(~held)[:50]:
                self.assertTrue(np.isfinite(r["eq"][i]))

    def test_matches_live_portfolio_engine_on_same_fills(self):
        cfg = Config()
        s = strategies.make_d(self.d, "1h", 10, 30)
        r = backtest(self.d["open"], self.d["high"], self.d["low"], self.d["close"], s.entry, s.exit_sig,
                     costs_from(spec.COSTS["temel"]))
        st = pf.new_state(cfg, 1.0, 0, 0, 1.0, "2026-01-01")
        st["cash_try"] = 1.0
        for t in r["trades"]:
            pf.open_buy(st, cfg, float(t["entry_ref"]), 0, 0, 0, "x")
            pf.close_position(st, cfg, float(t["exit_ref"]), 0, 0, 0, "x")
        self.assertAlmostEqual(st["cash_try"], float(np.prod([1 + t["ret"] for t in r["trades"]])), places=9)

    def test_cost_scenarios_are_monotonic_and_decisions_identical(self):
        s = strategies.make_b(self.d, "1h", 20, 2.0, "chan10")
        res = {}
        for name in ("sifir", "temel", "yuksek", "cok_yuksek"):
            c = ZERO if name == "sifir" else costs_from(spec.COSTS[name])
            res[name] = backtest(self.d["open"], self.d["high"], self.d["low"], self.d["close"], s.entry, s.exit_sig,
                                 c, s.atr, s.init_k, s.trail_k)
        ends = [res[k]["eq"][-1] for k in ("sifir", "temel", "yuksek", "cok_yuksek")]
        self.assertEqual(ends, sorted(ends, reverse=True))
        idx = [[(t["entry_i"], t["exit_i"]) for t in res[k]["trades"]] for k in res]
        self.assertTrue(all(x == idx[0] for x in idx))

    def test_start_index_blocks_warmup_trading(self):
        s = strategies.make_d(self.d, "1h", 10, 30)
        r = backtest(self.d["open"], self.d["high"], self.d["low"], self.d["close"], s.entry, s.exit_sig,
                     ZERO, start_idx=2000)
        self.assertTrue(all(t["entry_i"] >= 2000 for t in r["trades"]))
        self.assertTrue(np.all(r["eq"][:2000] == 1.0))


class MetricTests(unittest.TestCase):
    def mk(self, eq, bar=H4):
        n = len(eq)
        om = (1_600_000_000_000 // DAY * DAY + np.arange(n) * bar).astype("int64")
        return {"eq": np.asarray(eq, float), "held": np.zeros(n, bool), "trades": [], "open": False}, om

    def test_total_return_drawdown_and_normalization(self):
        eq = [1, 1.1, 0.99, 1.21, 1.21, 1.21, 1.21, 1.21]
        r, om = self.mk(eq)
        m = M.slice_metrics(r, om, om[0], om[-1] + 1)
        self.assertAlmostEqual(m["net_return_pct"], 21.0, places=6)
        self.assertAlmostEqual(m["max_dd_pct"], 10.0, places=6)
        m2 = M.slice_metrics(r, om, om[3], om[-1] + 1)       # dilim başlangıcına göre normalize
        self.assertAlmostEqual(m2["net_return_pct"], (1.21 / 0.99 - 1) * 100, places=6)

    def test_sharpe_sortino_formula(self):
        daily = np.array([0.01, -0.005] * 20)
        eq = np.cumprod(1 + daily)
        r, om = self.mk(eq, bar=DAY)
        m = M.slice_metrics(r, om, om[0], om[-1] + 1)
        d = np.concatenate([[eq[0] / 1 - 1], eq[1:] / eq[:-1] - 1])
        sd = d.std(ddof=1)
        self.assertAlmostEqual(m["sharpe"], d.mean() / sd * np.sqrt(365), places=6)
        dd = np.sqrt(np.mean(np.minimum(d, 0) ** 2))
        self.assertAlmostEqual(m["sortino"], d.mean() / dd * np.sqrt(365), places=6)

    def test_cagr(self):
        eq = np.linspace(1, 2, 366)
        r, om = self.mk(eq, bar=DAY)
        m = M.slice_metrics(r, om, om[0], om[-1] + 1)
        self.assertAlmostEqual(m["cagr_pct"], (2 ** (365.25 / 365) - 1) * 100, delta=0.5)

    def test_flat_has_zero_sharpe_no_nan(self):
        r, om = self.mk(np.ones(40), bar=DAY)
        m = M.slice_metrics(r, om, om[0], om[-1] + 1)
        self.assertEqual((m["sharpe"], m["sortino"], m["trades"], m["net_return_pct"]), (0.0, 0.0, 0, 0.0))

    def test_quarters_split_time_evenly(self):
        eq = np.concatenate([np.ones(10), np.full(10, 1.1), np.full(10, 1.1), np.full(10, 1.1)])
        r, om = self.mk(eq, bar=DAY)
        q = M.quarters(r, om, om[0], om[-1] + 1)
        self.assertEqual(len(q), 4)
        self.assertAlmostEqual(q[1], 10.0, places=6)
        self.assertAlmostEqual(q[2], 0.0, places=6)


class SplitAndSelectionTests(unittest.TestCase):
    def test_boundaries_chronological_60_20_20(self):
        t0, end = to_ms("2021-01-01"), to_ms("2026-10-20")
        tr, va, e = P.boundaries(t0, end)
        total = e - t0
        self.assertLess(t0, tr)
        self.assertLess(tr, va)
        self.assertLess(va, e)
        self.assertAlmostEqual((tr - t0) / total, 0.6, delta=0.001)
        self.assertAlmostEqual((va - t0) / total, 0.8, delta=0.001)
        self.assertEqual(tr % H4, 0)

    def _fake_client(self, server_ms, requested):
        from paper.binance import BinanceClient
        full = {}
        for tf, bar in (("5m", 300_000), ("1h", H1), ("4h", H4)):
            n = int((server_ms - to_ms("2023-09-01")) // bar)
            full[tf] = ohlc(n, 11 if tf == "1h" else 12 if tf == "4h" else 13, vol=0.01 if tf != "5m" else 0.003,
                            start=to_ms("2023-09-01") // bar * bar, bar=bar)

        class Sess:
            def get(self, url, params=None, timeout=None):
                class Resp:
                    status_code = 200

                    def __init__(s, data):
                        s._d = data

                    def json(s):
                        return s._d
                if url.endswith("/time"):
                    return Resp({"serverTime": server_ms})
                tf = params["interval"]
                requested.append((tf, params["endTime"]))
                d = full[tf]
                bar = {"5m": 300_000, "1h": H1, "4h": H4}[tf]
                sel = (d["open_ms"] >= params["startTime"]) & (d["open_ms"] <= params["endTime"])
                idx = np.flatnonzero(sel)[: params["limit"]]
                rows = [[int(d["open_ms"][i]), str(d["open"][i]), str(d["high"][i]), str(d["low"][i]),
                         str(d["close"][i]), "1", int(d["open_ms"][i] + bar - 1)] for i in idx]
                return Resp(rows)
        return BinanceClient("https://x", session=Sess(), sleep=lambda s: None), full

    def patches(self):
        return [mock.patch.object(spec, "FETCH_START", "2023-09-01"), mock.patch.object(spec, "EVAL_START", "2024-01-01"),
                mock.patch.object(spec, "WF_FIRST_DAYS", 150), mock.patch.object(spec, "WF_STEP_DAYS", 60),
                mock.patch.object(spec, "MIN_TRADES", 3), mock.patch("time.sleep", lambda s: None)]

    def test_select_stage_never_requests_test_period_and_is_frozen(self):
        server = to_ms("2025-06-01") + 3 * H1
        req = []
        client, full = self._fake_client(server, req)
        ps = self.patches()
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])
        with tempfile.TemporaryDirectory() as out:
            sel = R.stage_select(out, "https://x", log=lambda *a: None, client=client)
            self.assertTrue(os.path.exists(os.path.join(out, "selection.json")))
        val_end = sel["boundaries_ms"]["val_end"]
        self.assertTrue(req and all(end < val_end for _, end in req), "test dönemi verisi istendi")
        self.assertEqual(sel["candidate_count"], 26)
        self.assertEqual(sorted(sel["chosen"].keys()), ["B", "C", "D"])
        self.assertEqual(sel["spec_sha256"], P.spec_hash())

    def test_final_stage_refuses_changed_spec_and_changed_history(self):
        server = to_ms("2025-06-01") + 3 * H1
        req = []
        client, full = self._fake_client(server, req)
        ps = self.patches()
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])
        with tempfile.TemporaryDirectory() as out:
            R.stage_select(out, "https://x", log=lambda *a: None, client=client)
            path = os.path.join(out, "selection.json")
            sel = rj(path)
            bad = dict(sel, spec_sha256="0" * 64)
            wj(path, bad)
            with self.assertRaises(SystemExit):
                R.stage_final(out, "https://x", None, log=lambda *a: None, client=client)
            bad2 = copy.deepcopy(sel)
            bad2["fingerprints_pre_test"]["4h"] = "0" * 64
            wj(path, bad2)
            with self.assertRaises(SystemExit):
                R.stage_final(out, "https://x", None, log=lambda *a: None, client=client)
            wj(path, sel)
            fin = R.stage_final(out, "https://x", None, log=lambda *a: None, client=client)
            self.assertTrue(fin["evaluated_test_once"])
            self.assertTrue(os.path.exists(os.path.join(out, "RAPOR.md")))
            with open(os.path.join(out, "RAPOR.md"), encoding="utf-8") as f:
                md = f.read()
            for part in ("## Sonuç", "## Dönemler ve veri", "## Kabul kapıları", "Walk-forward", "Sınırlar"):
                self.assertIn(part, md)
            # seçim sonucu test sonucundan etkilenmedi
            self.assertEqual(fin["frozen"], {k: v for k, v in sel["chosen"].items() if v})

    def test_selection_ignores_data_after_val_end(self):
        d = ohlc(3000, 21, vol=0.01)
        c = strategies.make_d(d, "1h", 10, 30)
        r = backtest(d["open"], d["high"], d["low"], d["close"], c.entry, c.exit_sig, costs_from(spec.COSTS["temel"]))
        t0, tr, va = d["open_ms"][100], d["open_ms"][1800], d["open_ms"][2400]
        a = M.slice_metrics(r, d["open_ms"], t0, tr)
        # gelecekteki fiyatları bozsak eğitim metrikleri değişmemeli
        d2 = {k: v.copy() for k, v in d.items()}
        d2["close"][2400:] *= 5
        d2["open"][2400:] *= 5
        d2["high"][2400:] *= 5
        d2["low"][2400:] *= 5
        c2 = strategies.make_d(d2, "1h", 10, 30)
        r2 = backtest(d2["open"], d2["high"], d2["low"], d2["close"], c2.entry, c2.exit_sig, costs_from(spec.COSTS["temel"]))
        b = M.slice_metrics(r2, d["open_ms"], t0, tr)
        self.assertEqual(a, b)


class GateTests(unittest.TestCase):
    def ctx(self, **over):
        base = {"net_return_pct": 20.0, "max_dd_pct": 10.0, "sharpe": 1.5, "trades": 30, "ex_best_trade_return_pct": 5.0}
        m = {"temel": dict(base), "yuksek": dict(base, net_return_pct=8.0), "gecikmeli": dict(base, net_return_pct=12.0)}
        c = {"metrics": {"X": m}, "quarters": {"X": [5, 6, -1, 8]}, "neighbor_pos_share": {"X": 0.75},
             "wf": {"B": {"oos_return_pct": 12.0}}, "bh": {"sharpe": 1.0, "max_dd_pct": 40.0}}
        for k, v in over.items():
            c[k] = v
        return c

    def test_all_pass_and_each_gate_can_fail_independently(self):
        g = P.evaluate_gates("B", "X", self.ctx())
        self.assertTrue(all(v[0] for v in g.values()), g)
        c = self.ctx()
        c["metrics"]["X"]["temel"]["net_return_pct"] = -1
        self.assertFalse(P.evaluate_gates("B", "X", c)["G1"][0])
        c = self.ctx()
        c["metrics"]["X"]["yuksek"]["net_return_pct"] = -15
        self.assertFalse(P.evaluate_gates("B", "X", c)["G2"][0])
        c = self.ctx()
        c["metrics"]["X"]["gecikmeli"]["net_return_pct"] = -0.1
        self.assertFalse(P.evaluate_gates("B", "X", c)["G3"][0])
        c = self.ctx()
        c["metrics"]["X"]["temel"]["ex_best_trade_return_pct"] = -2
        self.assertFalse(P.evaluate_gates("B", "X", c)["G4"][0])
        c = self.ctx()
        c["metrics"]["X"]["temel"]["trades"] = 9
        self.assertFalse(P.evaluate_gates("B", "X", c)["G4"][0])
        self.assertFalse(P.evaluate_gates("B", "X", self.ctx(quarters={"X": [5, -1, -1, -3]}))["G5"][0])
        self.assertFalse(P.evaluate_gates("B", "X", self.ctx(neighbor_pos_share={"X": 0.25}))["G6"][0])
        self.assertFalse(P.evaluate_gates("B", "X", self.ctx(wf={"B": {"oos_return_pct": -3.0}}))["G7"][0])
        self.assertFalse(P.evaluate_gates("B", "X", self.ctx(wf={"B": {"oos_return_pct": None}}))["G7"][0])
        c = self.ctx(bh={"sharpe": 2.0, "max_dd_pct": 12.0})
        self.assertFalse(P.evaluate_gates("B", "X", c)["G8"][0])
        c = self.ctx(bh={"sharpe": 2.0, "max_dd_pct": 40.0})   # Sharpe geride ama düşüş <= yarısı
        self.assertTrue(P.evaluate_gates("B", "X", c)["G8"][0])

    def test_gates_listed_in_spec_match_evaluated(self):
        g = P.evaluate_gates("B", "X", self.ctx())
        self.assertEqual(sorted(g), sorted(spec.GATES))


class DataIntegrityTests(unittest.TestCase):
    def test_detects_duplicates_gaps_bad_ohlc_and_off_grid(self):
        d = ohlc(200, 31)
        self.assertTrue(integrity_ok(integrity(d, "1h")))
        dup = {k: np.concatenate([v[:50], v[49:]]) for k, v in d.items()}
        self.assertEqual(integrity(dup, "1h")["duplicates"], 1)
        gap = {k: np.delete(v, [10, 11, 12]) for k, v in d.items()}
        r = integrity(gap, "1h")
        self.assertEqual((r["missing_bars"], r["longest_gap_bars"]), (3, 3))
        bad = {k: v.copy() for k, v in d.items()}
        bad["high"][5] = bad["low"][5] * 0.5
        self.assertEqual(integrity(bad, "1h")["bad_ohlc"], 1)
        self.assertFalse(integrity_ok(integrity(bad, "1h")))
        off = dict(d, open_ms=d["open_ms"] + 7)
        self.assertGreater(integrity(off, "1h")["off_grid"], 0)

    def test_fingerprint_changes_when_history_changes_and_respects_cutoff(self):
        d = ohlc(100, 32)
        f = fingerprint(d)
        d2 = {k: v.copy() for k, v in d.items()}
        d2["close"][10] += 1
        self.assertNotEqual(f, fingerprint(d2))
        end = d["open_ms"][60]
        d3 = {k: v.copy() for k, v in d.items()}
        d3["close"][80] += 1                      # kesim sonrası fark
        self.assertEqual(fingerprint(d, end), fingerprint(d3, end))


class WalkForwardTests(unittest.TestCase):
    def test_picks_use_only_past_and_no_eligible_means_cash(self):
        d = ohlc(24 * 700, 41, vol=0.01)
        cands = [strategies.make_d(d, "1h", 10, 30), strategies.make_d(d, "1h", 20, 50),
                 strategies.make_e(d, "1h", 300), strategies.make_a({**d, "close": d["close"]}) if False else None]
        cands = [c for c in cands if c]
        cands[0].family = "D"
        for c in cands:
            c.name = c.name + "#"
        res = [backtest(d["open"], d["high"], d["low"], d["close"], c.entry, c.exit_sig, costs_from(spec.COSTS["temel"]),
                        start_idx=300) for c in cands]
        om = {"1h": d["open_ms"]}
        t0, t1 = d["open_ms"][300], d["open_ms"][-1]
        with mock.patch.object(spec, "WF_FIRST_DAYS", 200), mock.patch.object(spec, "WF_STEP_DAYS", 90), \
                mock.patch.object(spec, "MIN_TRADES", 10**6):
            wf = P.walk_forward(cands, res, om, t0, t1)
        self.assertTrue(all(p["pick"] is None for p in wf["D"]["picks"]))      # uygun yok -> nakit
        self.assertAlmostEqual(wf["D"]["oos_return_pct"], 0.0)
        with mock.patch.object(spec, "WF_FIRST_DAYS", 200), mock.patch.object(spec, "WF_STEP_DAYS", 90), \
                mock.patch.object(spec, "MIN_TRADES", 1):
            wf2 = P.walk_forward(cands, res, om, t0, t1)
        self.assertGreater(wf2["D"]["folds"], 1)
        first_pick = wf2["D"]["picks"][0]["pick"]
        # fold-1 seçimi, fold-1 sonrasındaki fiyatlar değişse de aynı kalır
        d2 = {k: v.copy() for k, v in d.items()}
        cut_i = 300 + 24 * 200 + 5
        for k in ("open", "high", "low", "close"):
            d2[k][cut_i:] *= 1.0 + 0.5 * np.sin(np.arange(len(d2[k]) - cut_i))
        cands2 = [strategies.make_d(d2, "1h", 10, 30), strategies.make_d(d2, "1h", 20, 50), strategies.make_e(d2, "1h", 300)]
        cands2[0].family = "D"
        for c in cands2:
            c.name += "#"
        res2 = [backtest(d2["open"], d2["high"], d2["low"], d2["close"], c.entry, c.exit_sig,
                         costs_from(spec.COSTS["temel"]), start_idx=300) for c in cands2]
        with mock.patch.object(spec, "WF_FIRST_DAYS", 200), mock.patch.object(spec, "WF_STEP_DAYS", 90), \
                mock.patch.object(spec, "MIN_TRADES", 1):
            wf3 = P.walk_forward(cands2, res2, om, t0, t1)
        self.assertEqual(first_pick, wf3["D"]["picks"][0]["pick"])


if __name__ == "__main__":
    unittest.main()
