"""Araştırma modülü (Aşama B) testleri: canlı botla parite, ileriye bakmama, maliyet ekonomisi."""
import os
import tempfile
import unittest

import numpy as np

from paper import portfolio as pf
from paper.config import Config
from paper.indicators import compute_row, signal_from_row
from research import engine as E
from research.analyze import (analyze, bootstrap_mean_ci, by_period, random_entry_baseline, render,
                              run_strategy, write_outputs)
from research.data import data_quality, fetch_klines
from tests.helpers import FakeClient
from tests.test_binance import END, FakeResp, FakeSession, rows_from
from paper.binance import BinanceClient

BAR = 300_000


def walk(n, seed=0, vol=0.002):
    rng = np.random.default_rng(seed)
    return 1_000_000 * np.exp(np.cumsum(rng.normal(0, vol, n)))


def times(n, start=1_640_995_200_000):
    return (start + np.arange(n) * BAR).astype("int64")


class SignalParityTests(unittest.TestCase):
    def test_vectorized_signals_match_live_functions(self):
        c = walk(4000, seed=3)
        om = times(len(c))
        buy, sell = E.signals(c, om)
        mismatches, seen = 0, 0
        for i in range(len(c)):
            sig = signal_from_row(compute_row(list(c[max(0, i - 59): i + 1])))
            mine = "buy" if buy[i] else "sell" if sell[i] else None
            seen += sig is not None
            mismatches += sig != mine
        self.assertGreater(seen, 100)
        self.assertEqual(mismatches, 0)

    def test_gap_invalidates_signals_until_window_is_complete(self):
        c = walk(600, seed=4, vol=0.004)
        om = times(len(c))
        om[300:] += BAR * 10  # 300. mumdan önce 10 mumluk boşluk
        buy, sell = E.signals(c, om)
        sig = buy | sell
        self.assertFalse(sig[300:349].any())  # boşluk sonrası 50 mum dolana kadar sinyal yok
        self.assertTrue(sig[350:].any())

    def test_no_lookahead_truncating_future_changes_nothing(self):
        c = walk(2000, seed=5, vol=0.004)
        om = times(len(c))
        b1, s1 = E.signals(c, om)
        b2, s2 = E.signals(c[:1200], om[:1200])
        self.assertTrue((b1[:1200] == b2).all() and (s1[:1200] == s2).all())

    def test_flat_series_gives_no_signal(self):
        c = np.full(300, 100.0)
        b, s = E.signals(c, times(300))
        self.assertFalse((b | s).any())


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.c = walk(6000, seed=6, vol=0.004)
        self.om = times(len(self.c))
        self.buy, self.sell = E.signals(self.c, self.om)

    def test_economics_match_live_portfolio_engine(self):
        cfg = Config()
        sim = E.simulate(self.c, self.buy, self.sell, E.BASE)
        self.assertGreater(len(sim["trades"]), 5)
        st = pf.new_state(cfg, 1.0, 0, 0, 1.0, "2026-01-01")
        st["cash_try"] = 1.0
        for i in np.flatnonzero(self.buy | self.sell):
            if self.buy[i]:
                pf.open_buy(st, cfg, float(self.c[i]), i, i, i, "t")
            elif self.sell[i]:
                pf.close_position(st, cfg, float(self.c[i]), i, i, i, "t")
        live_eq = pf.equity_try(st, cfg, float(self.c[-1]))
        self.assertAlmostEqual(sim["eq"][-1], live_eq, places=9)
        self.assertEqual(len(sim["trades"]), len(st["trades"]))

    def test_one_position_no_short_and_chronological(self):
        sim = E.simulate(self.c, self.buy, self.sell, E.BASE)
        prev = -1
        for t in sim["trades"]:
            self.assertLess(t["entry"], t["exit"])
            self.assertLessEqual(prev, t["entry"])
            prev = t["exit"]
            self.assertTrue(self.buy[t["sig_entry"]] and self.sell[t["sig_exit"]])

    def test_zero_cost_better_than_base_better_than_double(self):
        r = [E.simulate(self.c, self.buy, self.sell, k)["eq"][-1] for k in (E.ZERO, E.BASE, E.DOUBLE)]
        self.assertGreater(r[0], r[1])
        self.assertGreater(r[1], r[2])

    def test_latency_fills_at_later_close(self):
        s0 = E.simulate(self.c, self.buy, self.sell, E.ZERO, latency=0)
        s3 = E.simulate(self.c, self.buy, self.sell, E.ZERO, latency=3)
        t0, t3 = s0["trades"][0], s3["trades"][0]
        self.assertEqual(t3["entry"], t0["entry"] + 3)

    def test_equity_curve_consistent_with_trades(self):
        sim = E.simulate(self.c, self.buy, self.sell, E.BASE)
        c = 1.0
        for t in sim["trades"]:
            c = t["proceeds"]
        if sim["open"] is None:
            self.assertAlmostEqual(sim["eq"][-1], c, places=9)
        self.assertTrue(np.all(sim["eq"] > 0))
        self.assertEqual(sim["eq"][0], 1.0)
        self.assertGreaterEqual(E.max_drawdown_pct(sim["eq"]), 0.0)

    def test_open_position_marked_to_market_net_of_exit_costs(self):
        c = np.array([100.0] * 60 + [90.0, 95.0, 96.0])
        buy = np.zeros(len(c), dtype=bool)
        sell = np.zeros(len(c), dtype=bool)
        buy[61] = True
        sim = E.simulate(c, buy, sell, E.BASE)
        self.assertIsNotNone(sim["open"])
        expect = 1.0 / (1 + 0.001) / (95.0 * 1.0005) * 96.0 * 0.9995 * 0.999
        self.assertAlmostEqual(sim["eq"][-1], expect, places=12)

    def test_stats_with_no_trades(self):
        self.assertEqual(E.trade_stats([])["n"], 0)
        self.assertIsNone(random_entry_baseline({"trades": []}, self.c, E.BASE))
        self.assertIsNone(bootstrap_mean_ci([0.01]))


class RegimeTests(unittest.TestCase):
    def test_warmup_is_none_then_labels_by_trailing_return(self):
        n = 288 * 200
        om = times(n)
        up = 100 * np.exp(np.linspace(0, 1.0, n))  # güçlü yükseliş
        lab, ret = E.regime_labels(up, om)
        self.assertIsNone(lab[0])
        self.assertIsNone(lab[288 * 80])
        self.assertEqual(lab[-1], "yukselis")
        down = 100 * np.exp(np.linspace(0, -1.0, n))
        self.assertEqual(E.regime_labels(down, om)[0][-1], "dusus")
        flat = np.full(n, 100.0)
        self.assertEqual(E.regime_labels(flat, om)[0][-1], "yatay")

    def test_regime_uses_only_past_data(self):
        n = 288 * 200
        om = times(n)
        c = walk(n, seed=8, vol=0.0008)
        a, _ = E.regime_labels(c, om)
        b, _ = E.regime_labels(c[:n - 5000], om[:n - 5000])
        self.assertTrue(all(x == y for x, y in zip(a[:n - 5000], b)))


class DataTests(unittest.TestCase):
    def test_fetch_pages_until_exhausted_and_drops_forming_candle(self):
        from tests.helpers import candles_from_closes
        cs = candles_from_closes(list(walk(25, seed=9)), END)
        server = FakeResp(200, {"serverTime": END + 1000})
        pages = [FakeResp(200, rows_from(cs[:10])), FakeResp(200, rows_from(cs[10:20])),
                 FakeResp(200, rows_from(cs[20:])), FakeResp(200, [])]
        sess = FakeSession([server] + pages)
        cl = BinanceClient("https://data-api.binance.vision", session=sess, sleep=lambda x: None)
        om, o, h, l, c = fetch_klines("x", start_ms=cs[0].open_ms, client=cl, pause_s=0, log=lambda *a: None)
        self.assertEqual(len(om), 25)
        self.assertTrue(np.all(np.diff(om) == BAR))
        self.assertEqual(sess.urls[1][1]["startTime"], cs[0].open_ms)

    def test_data_quality_counts_missing(self):
        om = times(100)
        om = np.delete(om, [10, 11, 12, 50])
        q = data_quality(om)
        self.assertEqual(q["missing_candles"], 4)
        self.assertEqual(q["gap_count"], 2)
        self.assertEqual(q["longest_gap_candles"], 3)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_report_and_files_on_synthetic_data(self):
        n = 60_000
        c = walk(n, seed=11, vol=0.003)
        om = times(n)
        res = analyze(om, c, log=lambda *a: None)
        md = render(res)
        self.assertIn("kârlılık kanıtı değildir", md)
        import re
        self.assertIsNone(re.search(r"\bnan\b|\binf\b", md.lower()))
        for part in ("## Genel sonuç", "## Piyasa rejimine göre", "## Dönemlere göre", "## Parametre duyarlılığı",
                     "## Sınırlar", "gecikme=5"):
            self.assertIn(part, md)
        self.assertEqual(len(res["sensitivity"]), 27)
        # tablo satırlarında bozucu '|' içeren hücre adı yok
        for line in md.splitlines():
            if line.startswith("| ") and "gecikme" in line and "/" in line:
                self.assertEqual(line.count("|"), 8)
        with tempfile.TemporaryDirectory() as d:
            write_outputs(res, d)
            for f in ("RAPOR.md", "sonuclar.json", "duyarlilik.csv", "islemler.csv"):
                self.assertTrue(os.path.getsize(os.path.join(d, f)) > 0)

    def test_deterministic_given_same_data(self):
        c = walk(30_000, seed=12, vol=0.003)
        om = times(len(c))
        a = analyze(om, c, log=lambda *a: None)
        b = analyze(om, c, log=lambda *a: None)
        self.assertEqual(a["bootstrap"], b["bootstrap"])
        self.assertEqual(a["random_baseline"], b["random_baseline"])
        self.assertEqual(a["scenarios"], b["scenarios"])

    def test_periods_cover_years_and_halves(self):
        c = walk(288 * 800, seed=13, vol=0.002)
        om = times(len(c))
        rows = by_period(run_strategy(c, om), c, om)
        names = [r["period"] for r in rows]
        self.assertIn("1. yarı", names)
        self.assertIn("2. yarı", names)
        self.assertGreaterEqual(len([n for n in names if n.isdigit()]), 2)


if __name__ == "__main__":
    unittest.main()
