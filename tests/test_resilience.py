"""Eksik mum, mükerrerlik, sağlık/bekçi, bağımsız portföy ve kesinti senaryoları."""
import json
import math
import os
import random
import tempfile
import unittest
from fractions import Fraction

import requests

from paper import health
from paper import portfolio as pf
from paper.binance import BinanceClient, DataError, closed_tail
from paper.config import Config
from paper.runner import run_once
from paper.state import load_state, save_state
from paper.watchdog import decide
from paper.watchdog import main as watchdog_main
from tests.helpers import (CFG, IV, FakeClient, FakeNotifier, base_closes, buy_series,
                           candles_from_closes, ms_at, sell_series)
from tests.test_binance import END, FakeResp, FakeSession, rows_from

T0 = ms_at(2026, 10, 20, 8, 0)
HOLE = range(90, 100)


def trend(n):
    return [100 + 0.5 * i + 0.2 * math.sin(i) for i in range(n)]


def core(st):
    return {k: v for k, v in st.items() if k not in ("health", "outbox", "next_outbox_id", "gaps")}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "paper_state.json")
        self.n = FakeNotifier()

    def tearDown(self):
        self.d.cleanup()

    def run_with(self, client, init=False, wall_ms=T0):
        return run_once(CFG, client, self.n, self.p, init=init, wall_ms=wall_ms)

    def state(self):
        return load_state(self.p, CFG)

    def titles(self):
        return [t for t, _ in self.n.sent]

    def init_base(self):
        self.run_with(FakeClient(base_closes(), T0 + 80 * IV), init=True)


class GapTests(Base):
    def client_with_hole(self, n=200, with_backfill=True, hole=HOLE):
        closes = trend(n)
        c = FakeClient(closes, T0 + n * IV)
        full = list(c.all)
        c.all = [x for i, x in enumerate(full) if i not in hole]
        c.full = full if with_backfill else [x for i, x in enumerate(full) if i not in hole]
        return c

    def test_missing_candles_are_backfilled_and_processed_in_order(self):
        self.init_base()
        c = self.client_with_hole()
        res = self.run_with(c)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["new_candles"], 120)  # 80..199, boşluk dahil hepsi
        st = self.state()
        self.assertEqual(st["last_candle_open_ms"], T0 + 199 * IV)
        self.assertEqual([g["kind"] for g in st["gaps"]], ["filled"])
        self.assertIn("candles_between", c.calls)
        self.assertIn("Paper Mum Telafisi", self.titles())

    def test_unfillable_gap_generates_no_signals_for_gap_and_warns(self):
        self.init_base()
        c = self.client_with_hole(with_backfill=False)
        res = self.run_with(c)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["new_candles"], 100)  # yalnız boşluk SONRASI 100..199
        st = self.state()
        self.assertEqual(st["gaps"][-1]["kind"], "permanent")
        self.assertEqual(st["gaps"][-1]["missing"], 20)
        self.assertEqual(st["last_candle_open_ms"], T0 + 199 * IV)
        self.assertIn("Paper Mum Boslugu", self.titles())
        self.assertTrue(any("sinyal ÜRETİLMEDİ" in b for _, b in self.n.sent))

    def test_backfill_failure_halts_without_trade_or_state_advance_then_recovers(self):
        self.init_base()
        before = self.state()
        c = self.client_with_hole()
        c.backfill_error = DataError("zaman aşımı")
        for i in range(3):
            res = self.run_with(c, wall_ms=T0 + i * 60_000)
            self.assertEqual(res["status"], "gap_halt")
        st = self.state()
        self.assertEqual(st["last_candle_open_ms"], before["last_candle_open_ms"])
        self.assertEqual(st["trades"], [])
        self.assertEqual(st["health"]["consecutive_failures"], 3)
        self.assertEqual(self.titles().count("Paper Bot Hatasi"), 1)  # kısıtlı bildirim
        c.backfill_error = None
        res = self.run_with(c, wall_ms=T0 + 5 * 60_000)
        self.assertEqual(res["status"], "ok")
        self.assertIn("Paper Bot Toparlandi", self.titles())
        self.assertEqual(self.state()["health"]["consecutive_failures"], 0)

    def test_long_outage_beyond_api_window_is_backfilled(self):
        self.init_base()
        n = 80 + 360  # 30 saat durdu; Binance penceresi yalnız son 300 mum
        closes = trend(n)
        c = FakeClient(closes, T0 + n * IV)
        c.full = list(c.all)
        c.all = c.all[-300:]
        res = self.run_with(c)
        self.assertEqual(res["new_candles"], 360)
        self.assertEqual(self.state()["gaps"][-1]["kind"], "filled")

    def test_no_gap_when_candles_are_contiguous(self):
        self.init_base()
        c = FakeClient(trend(90), T0 + 90 * IV)
        res = self.run_with(c)
        self.assertEqual(self.state()["gaps"], [])
        self.assertNotIn("candles_between", c.calls)  # boşluk yokken telafi isteği yapılmaz
        self.assertEqual(res["new_candles"], 10)


class IdempotencyTests(Base):
    def test_rerunning_same_data_never_changes_results(self):
        self.init_base()
        series = sell_series()
        n = len(series)
        self.run_with(FakeClient(series, T0 + n * IV))
        first = self.state()
        sent = len(self.n.sent)
        self.assertEqual(len(first["trades"]), 1)
        for _ in range(3):
            res = self.run_with(FakeClient(series, T0 + n * IV))
            self.assertEqual(res["new_candles"], 0)
            self.assertEqual(res["events"], [])
        again = self.state()
        self.assertEqual(core(again), core(first))
        self.assertEqual(len(self.n.sent), sent)
        self.assertEqual(again["health"]["runs"], first["health"]["runs"] + 3)

    def test_one_run_and_many_runs_end_in_same_portfolio(self):
        series = sell_series()
        n = len(series)

        def final(steps):
            d = tempfile.TemporaryDirectory()
            p = os.path.join(d.name, "s.json")
            run_once(CFG, FakeClient(base_closes(), T0 + 80 * IV), FakeNotifier(), p, init=True, wall_ms=T0)
            for k in steps:
                run_once(CFG, FakeClient(series[:k], T0 + k * IV), FakeNotifier(), p, wall_ms=T0)
            st = load_state(p, CFG)
            d.cleanup()
            return st

        a = final([n])
        b = final([81, 82, 83, 84, n])
        for key in ("cash_try", "btc", "realized_pnl_try", "last_candle_open_ms"):
            self.assertAlmostEqual(a[key], b[key], places=6)
        self.assertEqual(len(a["trades"]), len(b["trades"]))
        self.assertEqual(len(a["trades"]), 1)
        for ta, tb in zip(a["trades"], b["trades"]):
            self.assertAlmostEqual(ta["net_pnl_try"], tb["net_pnl_try"], places=6)

    def test_one_signal_candle_never_trades_twice_even_if_state_rewound_partially(self):
        self.init_base()
        c = FakeClient(buy_series(), T0 + 81 * IV)
        self.run_with(c)
        self.assertEqual(self.state()["position"]["entry_candle_open_ms"], T0 + 80 * IV)
        for _ in range(2):
            self.run_with(c)
        st = self.state()
        self.assertEqual(len(st["trades"]), 0)
        self.assertEqual(self.titles().count("Paper Alis"), 1)


class HealthTests(Base):
    def test_success_records_run_candle_and_lag(self):
        self.run_with(FakeClient(base_closes(), T0 + 80 * IV), init=True)
        h = self.state()["health"]
        self.assertEqual(h["runs"], 1)
        self.assertEqual(h["last_success_ms"], T0)
        self.assertEqual(h["last_candle_open_ms"], T0 + 79 * IV)
        self.assertAlmostEqual(h["data_lag_s"], 10.0)
        self.assertEqual(h["consecutive_failures"], 0)

    def test_failures_counted_and_error_kept_short(self):
        self.init_base()
        c = FakeClient(base_closes(), T0 + 80 * IV)
        c.fail = DataError("x" * 1000)
        self.run_with(c)
        h = self.state()["health"]
        self.assertEqual((h["failures"], h["consecutive_failures"], h["max_consecutive_failures"]), (1, 1, 1))
        self.assertLessEqual(len(h["last_error"]), 200)

    def test_old_state_without_health_is_upgraded_not_rejected(self):
        self.init_base()
        st = self.state()
        del st["health"]
        del st["gaps"]
        save_state(self.p, st)
        self.run_with(FakeClient(base_closes(), T0 + 80 * IV))
        self.assertEqual(self.state()["health"]["runs"], 1)

    def test_bad_health_type_is_rejected_safely(self):
        self.init_base()
        st = self.state()
        st["health"] = "bozuk"
        save_state(self.p, st)
        res = self.run_with(FakeClient(base_closes(), T0 + 80 * IV))
        self.assertEqual(res["status"], "state_error")


class WatchdogTests(Base):
    def test_ok_when_recent(self):
        self.init_base()
        status, msg, alert = decide(self.p, CFG, T0 + 30 * 60_000)
        self.assertEqual((status, alert), ("ok", False))

    def test_stale_alerts_once_then_repeats_every_6h(self):
        self.init_base()
        over = lambda m: decide(self.p, CFG, T0 + (120 + m) * 60_000)  # noqa: E731
        self.assertEqual(over(-10)[0], "ok")
        s, msg, alert = over(10)
        self.assertEqual((s, alert), ("stale", True))
        self.assertIn("saattir", msg)
        self.assertFalse(over(70)[2])
        self.assertFalse(over(300)[2])
        self.assertTrue(over(360 + 10)[2])

    def test_missing_and_corrupt_state_alert_in_bucket(self):
        s, _, _ = decide(os.path.join(self.d.name, "yok.json"), CFG, T0)
        self.assertEqual(s, "missing")
        with open(self.p, "w") as f:
            f.write("{bozuk")
        s, msg, _ = decide(self.p, CFG, T0)
        self.assertEqual(s, "corrupt")

    def test_old_state_without_health_falls_back_to_last_candle(self):
        self.init_base()
        st = self.state()
        del st["health"]
        save_state(self.p, st)
        s, _, _ = decide(self.p, CFG, T0 + 80 * IV + 10 * 3600_000)
        self.assertEqual(s, "stale")

    def test_cli_dry_run_sends_nothing(self):
        self.init_base()
        self.assertEqual(watchdog_main(["--state", self.p, "--dry-run"]), 0)


class IndependentPortfolioTests(unittest.TestCase):
    """Beklenen değerler Fraction (tam kesir) ile, motordan farklı bir yoldan hesaplanır."""

    def test_round_trip_matches_exact_rational_math(self):
        rnd = random.Random(7)
        for _ in range(200):
            fb, fs = rnd.choice([0, 0.001, 0.00075]), rnd.choice([0, 0.001, 0.002])
            sb, ss = rnd.choice([0, 0.0005, 0.001]), rnd.choice([0, 0.0005, 0.002])
            cfg = Config(fee_buy=fb, fee_sell=fs, slippage_buy=sb, slippage_sell=ss)
            cash = rnd.uniform(100, 1e6)
            p_in, p_out = rnd.uniform(1e5, 5e6), rnd.uniform(1e5, 5e6)
            st = pf.new_state(cfg, 40.0, T0, T0, p_in, "2026-10-20")
            st["cash_try"] = cash
            pf.open_buy(st, cfg, p_in, T0, T0, T0, "t")
            # beklenen (kesirli): komisyon dahil toplam harcama = cash
            C, FB, SB = Fraction(cash), Fraction(fb), Fraction(sb)
            qty = (C / (1 + FB)) / (Fraction(p_in) * (1 + SB))
            self.assertAlmostEqual(st["btc"], float(qty), delta=float(qty) * 1e-12)
            # açık pozisyon değeri (satış maliyeti düşülmüş)
            mark = rnd.uniform(1e5, 5e6)
            liq = qty * Fraction(mark) * (1 - Fraction(ss)) * (1 - Fraction(fs))
            self.assertAlmostEqual(pf.equity_try(st, cfg, mark), float(liq), delta=float(liq) * 1e-12 + 1e-9)
            tr = pf.close_position(st, cfg, p_out, T0, T0, T0, "t")
            proceeds = qty * Fraction(p_out) * (1 - Fraction(ss)) * (1 - Fraction(fs))
            self.assertAlmostEqual(tr["net_pnl_try"], float(proceeds - C), delta=1e-6 + abs(float(proceeds)) * 1e-12)
            self.assertAlmostEqual(st["cash_try"], float(proceeds), delta=abs(float(proceeds)) * 1e-12 + 1e-9)
            self.assertEqual(st["btc"], 0.0)
            # maliyetsiz sonuç = qty * (çıkış ref - giriş ref)
            s = pf.stats(st)
            self.assertAlmostEqual(s["gross_pnl_try"], float(qty * (Fraction(p_out) - Fraction(p_in))),
                                   delta=1e-6 + abs(cash) * 1e-9)

    def test_stats_averages_profit_factor_and_costs(self):
        cfg = CFG
        st = pf.new_state(cfg, 40.0, T0, T0, 100.0, "2026-10-20")
        for p_in, p_out in [(100, 110), (100, 95), (100, 120), (100, 90)]:
            pf.open_buy(st, cfg, p_in, T0, T0, T0, "a")
            pf.close_position(st, cfg, p_out, T0, T0, T0, "b")
        s = pf.stats(st)
        self.assertEqual((s["closed"], s["wins"], s["losses"]), (4, 2, 2))
        pnls = [t["net_pnl_try"] for t in st["trades"]]
        self.assertAlmostEqual(s["avg_win_try"], (pnls[0] + pnls[2]) / 2)
        self.assertAlmostEqual(s["avg_loss_try"], (pnls[1] + pnls[3]) / 2)
        self.assertAlmostEqual(s["profit_factor"], (pnls[0] + pnls[2]) / -(pnls[1] + pnls[3]))
        self.assertGreater(s["fees_try"], 0)
        self.assertGreater(s["slippage_try"], 0)
        self.assertAlmostEqual(st["cash_try"] - st["initial"]["try"], s["net_pnl_try"], places=6)

    def test_no_loss_gives_no_profit_factor_and_no_trades_none(self):
        st = pf.new_state(CFG, 40.0, T0, T0, 100.0, "2026-10-20")
        self.assertIsNone(pf.stats(st)["avg_win_try"])
        pf.open_buy(st, CFG, 100, T0, T0, T0, "a")
        pf.close_position(st, CFG, 120, T0, T0, T0, "b")
        self.assertIsNone(pf.stats(st)["profit_factor"])

    def test_cash_never_negative_and_full_fill_only(self):
        st = pf.new_state(CFG, 40.0, T0, T0, 100.0, "2026-10-20")
        pf.open_buy(st, CFG, 100, T0, T0, T0, "a")
        self.assertEqual(st["cash_try"], 0.0)
        self.assertIsNone(pf.open_buy(st, CFG, 100, T0, T0, T0, "a"))  # kısmi/ikinci alım yok


class DataValidationAndFailureTests(unittest.TestCase):
    def client(self, responses):
        s = FakeSession(responses)
        sl = []
        return BinanceClient("https://api.binance.com", session=s, sleep=sl.append), s, sl

    def test_timeout_and_reset_are_retried_then_data_error(self):
        for exc in (requests.Timeout("t"), requests.exceptions.ChunkedEncodingError("reset"),
                    requests.ConnectionError("reset by peer")):
            c, s, _ = self.client([exc, exc, exc])
            with self.assertRaises(DataError):
                c.server_time_ms()
            self.assertEqual(len(s.urls), 3)

    def test_timeout_then_success(self):
        c, s, _ = self.client([requests.Timeout("t"), FakeResp(200, {"serverTime": 5})])
        self.assertEqual(c.server_time_ms(), 5)

    def test_http_429_uses_retry_after_capped(self):
        c, s, sl = self.client([FakeResp(429, headers={"Retry-After": "999"}), FakeResp(200, {"serverTime": 1})])
        c.server_time_ms()
        self.assertEqual(sl[0], 30)

    def test_exchange_error_body_and_http_400_are_data_errors(self):
        from tests.helpers import CFG as cfg
        c, _, _ = self.client([FakeResp(200, {"serverTime": 5}), FakeResp(200, {"code": -1121, "msg": "Invalid symbol."})])
        with self.assertRaises(DataError):
            c.closed_candles(cfg)
        c, _, _ = self.client([FakeResp(400, {"code": -1100})])
        with self.assertRaises(DataError):
            c.server_time_ms()

    def test_off_grid_timestamps_rejected(self):
        cs = candles_from_closes(base_closes(), END)
        shifted = [c._replace(open_ms=c.open_ms + 1) for c in cs]
        with self.assertRaises(DataError):
            closed_tail(shifted, END + 10_000, IV, 900, 60)

    def test_candles_between_pages_and_filters(self):
        cs = candles_from_closes(trend(25), END)
        cfg = Config(backfill_page_limit=10)
        s = FakeSession([FakeResp(200, rows_from(cs[0:10])), FakeResp(200, rows_from(cs[10:20])),
                         FakeResp(200, rows_from(cs[20:25]))])
        c = BinanceClient("https://api.binance.com", session=s, sleep=lambda x: None)
        got = c.candles_between(cfg, cs[0].open_ms, cs[-1].open_ms, END + 10_000)
        self.assertEqual([x.open_ms for x in got], [x.open_ms for x in cs])
        self.assertEqual(s.urls[1][1]["startTime"], cs[9].open_ms + IV)
        self.assertTrue(all(u[1]["symbol"] == "BTCTRY" for u in s.urls))

    def test_candles_between_drops_unclosed_and_rejects_duplicates(self):
        cs = candles_from_closes(trend(5), END)
        s = FakeSession([FakeResp(200, rows_from(cs))])
        c = BinanceClient("https://api.binance.com", session=s, sleep=lambda x: None)
        got = c.candles_between(Config(), cs[0].open_ms, cs[-1].open_ms, cs[-1].open_ms + 1000)
        self.assertEqual(len(got), 4)  # son mum henüz kapanmadı
        s = FakeSession([FakeResp(200, rows_from(cs + [cs[2]]))])
        c = BinanceClient("https://api.binance.com", session=s, sleep=lambda x: None)
        with self.assertRaises(DataError):
            c.candles_between(Config(), cs[0].open_ms, cs[-1].open_ms, END + 10_000)

    def test_backfill_bad_response_is_data_error(self):
        s = FakeSession([FakeResp(200, {"code": -1})])
        c = BinanceClient("https://api.binance.com", session=s, sleep=lambda x: None)
        with self.assertRaises(DataError):
            c.candles_between(Config(), 0, IV * 3, END)

    def test_backfill_still_whitelisted_get_only(self):
        s = FakeSession([FakeResp(200, [])])
        c = BinanceClient("https://api.binance.com", session=s, sleep=lambda x: None)
        c.candles_between(Config(), 0, IV, END)
        self.assertTrue(all("klines" in u[0] for u in s.urls))


class ConfigTests(unittest.TestCase):
    def test_watchdog_limit_validated(self):
        from paper.config import ConfigError, from_env
        with self.assertRaises(ConfigError):
            from_env({"PAPER_WATCHDOG_MAX_AGE_MIN": "5"})
        self.assertEqual(from_env({"PAPER_WATCHDOG_MAX_AGE_MIN": "90"}).watchdog_max_age_min, 90)


if __name__ == "__main__":
    unittest.main()
