import json
import os
import tempfile
import unittest
from datetime import timedelta

from paper import portfolio as pf
from paper.binance import DataError
from paper.indicators import compute_row, signal_from_row
from paper.notify import Notifier
from paper.runner import error_notice, run_once
from paper.state import load_state, save_state
from tests.helpers import (CFG, IV, FakeClient, FakeNotifier, base_closes, buy_series, ms_at,
                           sell_series)

# 20 Ekim 2026 08:00 TSİ: rapor saatinden (21:00) önce
T0 = ms_at(2026, 10, 20, 8, 0)


def close_ms_for(n_candles):
    """n mumluk seri, T0'dan başlayıp son mumu bu anda kapanacak şekilde."""
    return T0 + n_candles * IV


class RunnerBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "paper_state.json")
        self.n = FakeNotifier()

    def tearDown(self):
        self.d.cleanup()

    def run_with(self, closes, last_close_ms=None, init=False, notifier=None, client=None, **kw):
        client = client or FakeClient(closes, last_close_ms or close_ms_for(len(closes)))
        res = run_once(CFG, client, notifier or self.n, self.p, init=init, wall_ms=T0, **kw)
        return res, client

    def state(self):
        return load_state(self.p, CFG)


class InitAndTradingTests(RunnerBase):
    def test_init_creates_state_and_announces_paper_mode_without_trading(self):
        res, _ = self.run_with(base_closes(), init=True)
        self.assertTrue(res["initialized"])
        s = self.state()
        self.assertEqual(s["initial"]["usdt"], 1000.0)
        self.assertAlmostEqual(s["cash_try"], 40_000.0)
        self.assertEqual(s["trades"], [])
        titles = [t for t, _ in self.n.sent]
        self.assertEqual(titles, ["Paper Bot Basladi"])
        self.assertIn("PAPER", self.n.sent[0][1])
        self.assertEqual(s["outbox"], [])

    def test_missing_state_without_init_stops_and_does_not_create(self):
        res, client = self.run_with(base_closes())
        self.assertEqual(res["status"], "state_error")
        self.assertFalse(os.path.exists(self.p))
        self.assertEqual(client.calls, [])  # veriye bile gitmedi

    def test_buy_signal_opens_virtual_position_once(self):
        self.run_with(base_closes(), init=True)
        res, _ = self.run_with(buy_series())
        self.assertEqual([e[0] for e in res["events"]], ["buy"])
        s = self.state()
        self.assertIsNotNone(s["position"])
        self.assertAlmostEqual(s["position"]["entry_ref_price"], buy_series()[-1])
        self.assertEqual(self.n.sent[-1][0], "Paper Alis")
        # aynı mum ikinci kez işlenmez
        before = self.state()
        sent = len(self.n.sent)
        res2, _ = self.run_with(buy_series())
        self.assertEqual(res2["new_candles"], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.n.sent), sent)

    def test_sell_closes_position_and_reports_pnl(self):
        self.run_with(base_closes(), init=True)
        self.run_with(buy_series())
        res, _ = self.run_with(sell_series())
        kinds = [e[0] for e in res["events"]]
        self.assertEqual(kinds, ["sell"])
        s = self.state()
        self.assertIsNone(s["position"])
        self.assertEqual(len(s["trades"]), 1)
        title, body = self.n.sent[-1]
        self.assertEqual(title, "Paper Satis")
        self.assertIn("Net K/Z", body)
        self.assertIn("Getiri", body)

    def test_sell_signal_without_position_opens_no_short(self):
        s = base_closes()
        k = 1.0
        while signal_from_row(compute_row(s + [s[-1] + k])) != "sell":
            k += 0.5
        s = s + [s[-1] + k]                     # yükseliş + üst bant sıçraması, hiç alım yok
        self.run_with(s[:70], init=True)
        res, _ = self.run_with(s)
        self.assertTrue(any("short açılmaz" in n for n in res["notes"]))
        st = self.state()
        self.assertEqual(st["trades"], [])
        self.assertIsNone(st["position"])
        self.assertEqual(st["btc"], 0.0)
        self.assertAlmostEqual(st["cash_try"], 40_000.0)

    def test_catch_up_processes_missed_candles_in_order(self):
        self.run_with(base_closes(), init=True)
        # 1 çalıştırma atlandı: alım ve satış mumları arada kaldı; son çalıştırma hepsini görür
        res, _ = self.run_with(sell_series())
        kinds = [e[0] for e in res["events"]]
        # alım mumu bayat (>30 dk) olabilir; en azından mükerrer/ters sıra olmaz
        self.assertTrue(kinds in ([], ["sell"], ["buy", "sell"]))
        s = self.state()
        self.assertLessEqual(len(s["trades"]), 1)

    def test_stale_signal_is_not_traded(self):
        self.run_with(base_closes(), init=True)
        s = buy_series() + [buy_series()[-1] + 0.1 * i for i in range(1, 9)]  # alım mumu 8 mum önce
        res, _ = self.run_with(s)
        st = self.state()
        self.assertIsNone(st["position"])
        self.assertGreaterEqual(st["skipped_stale_signals"], 1)

    def test_no_lookahead_decision_independent_of_future_candles(self):
        self.run_with(base_closes(), init=True)
        self.run_with(buy_series())
        entry_now = self.state()["position"]["entry_price"]
        # Aynı karar, sonraki mumlar eklenmiş seriyle yeniden kurulan portföyde aynı olmalı
        d2 = tempfile.TemporaryDirectory()
        try:
            p2 = os.path.join(d2.name, "s.json")
            run_once(CFG, FakeClient(base_closes(), close_ms_for(80)), FakeNotifier(), p2, init=True, wall_ms=T0)
            later = buy_series() + [buy_series()[-1] + 0.05]   # 1 mum sonrası da görünür
            run_once(CFG, FakeClient(later, close_ms_for(82)), FakeNotifier(), p2, wall_ms=T0)
            pos = load_state(p2, CFG)["position"]
            self.assertAlmostEqual(pos["entry_price"], entry_now)
        finally:
            d2.cleanup()


class DataAndNotifyFailureTests(RunnerBase):
    def test_binance_error_waits_safely_without_trade_or_state_change(self):
        self.run_with(base_closes(), init=True)
        with open(self.p, encoding="utf-8") as f:
            before = f.read()
        client = FakeClient(buy_series(), close_ms_for(81))
        client.fail = DataError("HTTP 451")
        res, _ = self.run_with(buy_series(), client=client)
        self.assertEqual(res["status"], "data_error")
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.n.sent[-1][0], "Paper Bot Hatasi")

    def test_stale_exchange_data_waits(self):
        self.run_with(base_closes(), init=True)
        client = FakeClient(buy_series(), close_ms_for(81), lag_ms=3_600_000)  # 1 saat bayat
        res, _ = self.run_with(buy_series(), client=client)
        self.assertEqual(res["status"], "data_error")
        self.assertIsNone(self.state()["position"])

    def test_error_notifications_are_throttled(self):
        self.run_with(base_closes(), init=True)
        client = FakeClient(buy_series(), close_ms_for(81))
        client.fail = DataError("x")
        for _ in range(5):
            self.run_with(buy_series(), client=client)
        errs = [t for t, _ in self.n.sent if t == "Paper Bot Hatasi"]
        self.assertEqual(len(errs), 1)

    def test_corrupt_state_stops_notifies_and_keeps_file(self):
        with open(self.p, "w") as f:
            f.write("{bozuk")
        res, client = self.run_with(base_closes(), init=True)  # init bile olsa ezmez
        self.assertEqual(res["status"], "state_error")
        with open(self.p) as f:
            self.assertEqual(f.read(), "{bozuk")
        self.assertEqual(self.n.sent[-1][0], "Paper Bot Hatasi")

    def test_ntfy_failure_does_not_stop_bot_and_message_is_retried(self):
        self.run_with(base_closes(), init=True)
        bad = FakeNotifier(ok=False)
        res, _ = self.run_with(buy_series(), notifier=bad)
        self.assertEqual(res["status"], "ok")
        s = self.state()
        self.assertIsNotNone(s["position"])          # işlem kaydedildi
        self.assertEqual(len(s["outbox"]), 1)         # bildirim bekliyor
        res2, _ = self.run_with(buy_series())         # bildirim sistemi düzeldi
        self.assertEqual(len(self.state()["outbox"]), 0)
        self.assertEqual([t for t, _ in self.n.sent].count("Paper Alis"), 1)
        self.assertIsNotNone(self.state()["position"])
        self.assertEqual(len(self.state()["trades"]), 0)  # mükerrer işlem yok

    def test_crash_after_save_before_send_does_not_duplicate_trade(self):
        self.run_with(base_closes(), init=True)

        class Boom(FakeNotifier):
            def send(self, *a, **k):
                raise RuntimeError("çöktü")

        with self.assertRaises(RuntimeError):
            self.run_with(buy_series(), notifier=Boom())
        s = self.state()                     # state diske yazılmıştı
        self.assertIsNotNone(s["position"])
        qty = s["position"]["qty"]
        self.run_with(buy_series())          # yeniden başlatma
        s2 = self.state()
        self.assertEqual(s2["position"]["qty"], qty)   # ikinci alım yok
        self.assertEqual(len(s2["outbox"]), 0)         # bekleyen bildirim gitti

    def test_dry_run_writes_nothing_and_sends_nothing(self):
        res, _ = self.run_with(base_closes(), init=True, dry_run=True)
        self.assertFalse(os.path.exists(self.p))
        self.assertEqual(self.n.sent, [])


class ReportTests(RunnerBase):
    def setUp(self):
        super().setUp()
        self.run_with(base_closes(), init=True)       # 08:00 + 6,7 saat = ~14:40 TSİ
        self.run_with(buy_series())

    def evening_run(self, day_offset=0, hour=21, minute=30):
        end = ms_at(2026, 10, 20 + day_offset, hour, minute)
        # son kapanış end; seri bu uçta biter, bot state'teki mumlardan sonrasını görür
        return self.run_with(buy_series() + [buy_series()[-1] + 0.02 * i for i in range(1, 3)],
                             last_close_ms=end)

    def reports(self):
        return [m for t, m in self.n.sent if t == "Paper Gunluk Rapor"]

    def test_no_report_before_report_hour(self):
        self.assertEqual(self.reports(), [])

    def test_report_sent_once_per_day_and_has_required_fields(self):
        self.evening_run()
        self.evening_run(minute=40)
        self.evening_run(minute=50)
        reps = self.reports()
        self.assertEqual(len(reps), 1)
        body = reps[0]
        for needle in ("PAPER", "BTCTRY", "Toplam portföy", "USDT", "Toplam net K/Z",
                       "Gerçekleşmiş", "Gerçekleşmemiş", "Nakit", "BTC:", "Bugün kapanan",
                       "kazanma oranı", "Maks. drawdown", "kur etkisi"):
            self.assertIn(needle, body, needle)

    def test_report_next_day_and_missed_report_is_sent_late(self):
        self.evening_run()
        self.evening_run(day_offset=1, hour=9, minute=0)   # ertesi sabah: rapor saati gelmedi
        self.assertEqual(len(self.reports()), 1)
        self.evening_run(day_offset=1, hour=23, minute=10)  # rapor saatinde kapalıydı; geç gönderilir
        self.assertEqual(len(self.reports()), 2)

    def test_fx_effect_is_separate_from_strategy_pnl(self):
        client = FakeClient(buy_series(), ms_at(2026, 10, 20, 21, 30), rate=44.0)  # kur %10 arttı
        res, _ = self.run_with(buy_series(), last_close_ms=ms_at(2026, 10, 20, 21, 30), client=client)
        body = self.reports()[0]
        # strateji sonucu başlangıç kuruyla; güncel kur satırı ayrı
        self.assertIn("başlangıç 40.0000", body)
        self.assertIn("güncel 44.0000", body)
        s = self.state()
        eq = pf.equity_try(s, CFG, s["last_price"])
        self.assertIn(f"{eq / 40.0:,.2f} USDT (başlangıç kuru)", body)


class NotifierTests(unittest.TestCase):
    class S:
        def __init__(self, resp=None, exc=None):
            self.calls = []
            self.resp, self.exc = resp, exc

        def post(self, url, data=None, headers=None, timeout=None):
            self.calls.append((url, data, headers))
            if self.exc:
                raise self.exc
            return self.resp

    class R:
        def __init__(self, c):
            self.status_code = c

    def test_title_is_ascii_and_payload_utf8_like_before(self):
        s = self.S(resp=self.R(200))
        n = Notifier("gizli-topic", session=s, sleep=lambda x: None)
        self.assertTrue(n.send("Fiyat: ₺1", title="Paper Günlük Rapor ı"))
        url, data, headers = s.calls[0]
        self.assertTrue(url.endswith("/gizli-topic"))
        headers["Title"].encode("ascii")
        self.assertEqual(headers["Priority"], "high")
        self.assertEqual(data.decode("utf-8"), "Fiyat: ₺1")

    def test_three_attempts_then_false_never_raises_and_topic_not_logged(self):
        s = self.S(exc=RuntimeError("bağlanamadı https://ntfy.sh/gizli-topic"))
        n = Notifier("gizli-topic", session=s, sleep=lambda x: None)
        logs = []
        n.log = logs.append
        self.assertFalse(n.send("x"))
        self.assertEqual(len(s.calls), 3)
        self.assertFalse(any("gizli-topic" in m for m in logs))

    def test_missing_topic_does_not_crash(self):
        n = Notifier("", session=self.S(resp=self.R(200)))
        n.log = lambda m: None
        self.assertFalse(n.send("x"))


if __name__ == "__main__":
    unittest.main()


class LongRunInvariantTests(RunnerBase):
    def test_oscillating_series_700_candles_keeps_invariants(self):
        import math
        import random
        random.seed(3)
        full = [1000 + 3.0 * i + 60 * math.sin(i / 8) + random.gauss(0, 9) for i in range(780)]
        closes = full[:80]
        self.run_with(closes, init=True)
        total_events = 0
        for k in range(80, 780):
            closes.append(full[k])
            res, _ = self.run_with(closes[-300:], last_close_ms=close_ms_for(len(closes)))
            self.assertEqual(res["status"], "ok")
            total_events += len(res["events"])
            s = self.state()                                   # her adımda state geçerli
            self.assertGreaterEqual(s["cash_try"], 0)
            self.assertEqual(s["position"] is not None, s["btc"] > 0)
            price = s["last_price"]
            self.assertAlmostEqual(
                pf.total_pnl_try(s, CFG, price),
                s["realized_pnl_try"] + pf.unrealized_pnl_try(s, CFG, price), places=4)
            self.assertAlmostEqual(s["realized_pnl_try"], sum(t["net_pnl_try"] for t in s["trades"]), places=4)
        s = self.state()
        ids = [t["entry_candle_open_ms"] for t in s["trades"]]
        self.assertEqual(len(ids), len(set(ids)))              # aynı girişten iki işlem yok
        for t in s["trades"]:
            self.assertLess(t["entry_candle_open_ms"], t["exit_candle_open_ms"])
        self.assertGreaterEqual(len(s["trades"]), 3)           # test gerçekten işlem içeriyor
        print(f"[uzun test] işlem olayı={total_events}, kapanan={len(s['trades'])}, "
              f"maxDD=%{s['max_drawdown_pct']:.2f}")
