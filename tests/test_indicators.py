import math
import random
import unittest

from paper.indicators import compute_row, signal_from_row
from tests.helpers import base_closes, buy_series, sell_series

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


def old_bot_signals(closes):
    """Eski bot.py'deki pandas formüllerinin birebir kopyası (referans)."""
    df = pd.DataFrame({"Close": closes})
    df["SMA_20"] = df["Close"].rolling(window=20).mean()
    df["STD"] = df["Close"].rolling(window=20).std()
    df["Upper_Band"] = df["SMA_20"] + (df["STD"] * 2)
    df["Lower_Band"] = df["SMA_20"] - (df["STD"] * 2)
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    df["RSI"] = 100 - (100 / (1 + (gain / loss)))
    return df


class IndicatorTests(unittest.TestCase):
    def test_not_enough_data_gives_no_signal(self):
        self.assertIsNone(compute_row(base_closes()[:49]))
        self.assertIsNone(signal_from_row(None))

    def test_flat_prices_give_no_signal(self):
        self.assertIsNone(signal_from_row(compute_row([100.0] * 60)))  # RSI NaN -> sinyal yok

    def test_known_buy_and_sell(self):
        self.assertEqual(signal_from_row(compute_row(buy_series())), "buy")
        self.assertEqual(signal_from_row(compute_row(sell_series())), "sell")

    @unittest.skipIf(pd is None, "pandas yok")
    def test_parity_with_old_pandas_formulas(self):
        random.seed(7)
        closes = [3_000_000.0]
        for _ in range(400):
            closes.append(closes[-1] * (1 + random.gauss(0, 0.004)))
        df = old_bot_signals(closes)
        compared = buys = sells = 0
        for i in range(60, len(closes)):
            row = compute_row(closes[: i + 1])
            ref = df.iloc[i]
            for name, val in (("SMA_20", row.sma20), ("Upper_Band", row.upper),
                              ("Lower_Band", row.lower), ("SMA_50", row.sma50), ("RSI", row.rsi)):
                self.assertTrue(math.isclose(val, float(ref[name]), rel_tol=1e-9, abs_tol=1e-6),
                                f"{name} i={i}: {val} != {ref[name]}")
            old_buy = ref["Close"] <= ref["Lower_Band"] and ref["RSI"] < 48 and ref["Close"] > ref["SMA_50"]
            old_sell = (not old_buy) and ref["Close"] >= ref["Upper_Band"]
            expected = "buy" if old_buy else ("sell" if old_sell else None)
            self.assertEqual(signal_from_row(row), expected, f"i={i}")
            compared += 1
            buys += expected == "buy"
            sells += expected == "sell"
        self.assertGreater(compared, 300)
        self.assertGreater(buys + sells, 0)  # test gerçekten sinyal içeriyor


if __name__ == "__main__":
    unittest.main()
