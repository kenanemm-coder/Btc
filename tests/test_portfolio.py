import unittest

from paper import portfolio as pf
from paper.config import Config
from tests.helpers import CFG

T = 1_760_000_000_000


def fresh(rate=40.0, cfg=CFG):
    return pf.new_state(cfg, rate, T, T - 300_000, 3_000_000.0, "2026-10-20")


class PortfolioTests(unittest.TestCase):
    def test_initial_balance_converted_with_usdt_try(self):
        s = fresh(rate=41.5)
        self.assertEqual(s["initial"]["usdt"], 1000.0)
        self.assertAlmostEqual(s["initial"]["try"], 41500.0)
        self.assertAlmostEqual(s["cash_try"], 41500.0)
        self.assertEqual(s["btc"], 0.0)
        self.assertEqual(s["mode"], "PAPER")

    def test_buy_fee_and_slippage(self):
        s = fresh(rate=40.0)  # 40.000 TRY
        pos = pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        fill = 3_000_000.0 * 1.0005            # slipaj fiyatı yükseltir
        notional = 40_000.0 / 1.001            # komisyon %0,10
        self.assertAlmostEqual(pos["entry_price"], fill)
        self.assertAlmostEqual(pos["qty"], notional / fill, places=12)
        self.assertAlmostEqual(pos["entry_fee_try"], notional * 0.001)
        self.assertAlmostEqual(pos["cost_try"], 40_000.0)
        self.assertEqual(s["cash_try"], 0.0)
        self.assertAlmostEqual(s["btc"], pos["qty"])

    def test_sell_fee_and_slippage_and_pnl(self):
        s = fresh(rate=40.0)
        pos = pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        tr = pf.close_position(s, CFG, 3_100_000.0, 2, T, T, "t")
        fill = 3_100_000.0 * (1 - 0.0005)      # slipaj fiyatı düşürür
        gross = pos["qty"] * fill
        fee = gross * 0.001
        self.assertAlmostEqual(tr["exit_price"], fill)
        self.assertAlmostEqual(tr["exit_fee_try"], fee)
        self.assertAlmostEqual(tr["net_pnl_try"], gross - fee - 40_000.0, places=6)
        self.assertAlmostEqual(tr["return_pct"], tr["net_pnl_try"] / 40_000.0 * 100)
        self.assertAlmostEqual(s["cash_try"], gross - fee, places=6)
        self.assertAlmostEqual(s["realized_pnl_try"], tr["net_pnl_try"], places=6)
        self.assertEqual(s["btc"], 0.0)
        self.assertIsNone(s["position"])

    def test_losing_trade_net_negative_even_if_price_flat(self):
        s = fresh()
        pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        tr = pf.close_position(s, CFG, 3_000_000.0, 2, T, T, "t")
        self.assertLess(tr["net_pnl_try"], 0)  # maliyetler kaybettirir
        st = pf.stats(s)
        self.assertEqual((st["closed"], st["wins"], st["losses"]), (1, 0, 1))

    def test_unrealized_pnl_includes_exit_costs(self):
        s = fresh()
        pos = pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        price = 3_050_000.0
        expected = pos["qty"] * price * (1 - 0.0005) * (1 - 0.001) - 40_000.0
        self.assertAlmostEqual(pf.unrealized_pnl_try(s, CFG, price), expected, places=6)

    def test_total_return_and_equity_with_costs(self):
        s = fresh()
        self.assertAlmostEqual(pf.total_pnl_try(s, CFG, 3_000_000.0), 0.0)
        pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        price = 3_000_000.0
        eq = pf.equity_try(s, CFG, price)
        self.assertAlmostEqual(eq, s["btc"] * price * 0.9995 * 0.999, places=6)
        self.assertAlmostEqual(pf.total_pnl_try(s, CFG, price), eq - 40_000.0, places=6)
        # toplam = gerçekleşmiş + gerçekleşmemiş
        self.assertAlmostEqual(
            pf.total_pnl_try(s, CFG, 3_100_000.0),
            s["realized_pnl_try"] + pf.unrealized_pnl_try(s, CFG, 3_100_000.0), places=6)

    def test_no_short_when_flat(self):
        s = fresh()
        self.assertIsNone(pf.close_position(s, CFG, 3_000_000.0, 1, T, T, "t"))
        self.assertEqual(s["btc"], 0.0)
        self.assertEqual(s["trades"], [])
        self.assertAlmostEqual(s["cash_try"], 40_000.0)

    def test_second_buy_while_holding_is_ignored(self):
        s = fresh()
        pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        qty = s["btc"]
        self.assertIsNone(pf.open_buy(s, CFG, 2_900_000.0, 2, T, T, "t"))
        self.assertEqual(s["btc"], qty)

    def test_costs_are_configurable(self):
        cfg = Config(fee_buy=0.0, fee_sell=0.0, slippage_buy=0.0, slippage_sell=0.0)
        s = fresh(cfg=cfg)
        pf.open_buy(s, cfg, 3_000_000.0, 1, T, T, "t")
        tr = pf.close_position(s, cfg, 3_000_000.0, 2, T, T, "t")
        self.assertAlmostEqual(tr["net_pnl_try"], 0.0, places=6)

    def test_drawdown_tracking(self):
        s = fresh()
        pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        pf.mark_equity(s, CFG, 3_300_000.0, "2026-10-20")
        pf.mark_equity(s, CFG, 2_970_000.0, "2026-10-20")
        self.assertGreater(s["max_drawdown_pct"], 9.9)
        self.assertEqual(len(s["daily_equity"]), 1)


if __name__ == "__main__":
    unittest.main()
