import unittest

import requests

from paper.binance import (ALLOWED_PATHS, BinanceClient, Candle, DataError, closed_tail,
                           parse_klines)
from tests.helpers import CFG, IV, base_closes, candles_from_closes

END = 1_760_000_000_000


class FakeResp:
    def __init__(self, code=200, data=None, headers=None):
        self.status_code = code
        self._data = data
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("json yok")
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def post(self, *a, **k):
        raise AssertionError("Binance istemcisi POST yapmamalı")


def rows_from(candles):
    return [[c.open_ms, str(c.open), str(c.high), str(c.low), str(c.close), "1", c.close_ms]
            for c in candles]


class ClosedCandleTests(unittest.TestCase):
    def test_forming_candle_is_dropped(self):
        cs = candles_from_closes(base_closes(), END)
        forming = Candle(END, 1, 2, 1, 1.5, END + IV - 1)   # henüz kapanmamış
        tail = closed_tail(cs + [forming], END + 60_000, IV, 900, 60)
        self.assertEqual(tail[-1].open_ms, cs[-1].open_ms)
        self.assertNotIn(forming, tail)

    def test_gap_keeps_only_contiguous_tail_and_waits_if_short(self):
        cs = candles_from_closes(base_closes(), END)
        gapped = cs[:30] + cs[32:]           # ortada 2 mumluk boşluk
        with self.assertRaises(DataError):   # son kesit 48 mum < 60
            closed_tail(gapped, END + 10_000, IV, 900, 60)

    def test_stale_data_waits(self):
        cs = candles_from_closes(base_closes(), END)
        with self.assertRaises(DataError):
            closed_tail(cs, END + 3_600_000, IV, 900, 60)

    def test_duplicate_or_unsorted_rejected(self):
        cs = candles_from_closes(base_closes(), END)
        with self.assertRaises(DataError):
            closed_tail(cs + [cs[-1]], END + 10_000, IV, 900, 60)

    def test_too_few_candles(self):
        cs = candles_from_closes(base_closes()[:30], END)
        with self.assertRaises(DataError):
            closed_tail(cs, END + 10_000, IV, 900, 60)

    def test_invalid_ohlc_rejected(self):
        good = [1, "10", "11", "9", "10", "1", 2]
        for bad in ([1, "10", "11", "9", "nan", "1", 2], [1, "10", "9", "11", "10", "1", 2],
                    [1, "10", "11", "9", "-5", "1", 2], [1, "10", "11", "9", "12", "1", 2],
                    [1, "x", "11", "9", "10", "1", 2], [1]):
            with self.assertRaises(DataError, msg=str(bad)):
                parse_klines([bad])
        self.assertEqual(len(parse_klines([good])), 1)


class ClientTests(unittest.TestCase):
    def client(self, responses):
        s = FakeSession(responses)
        sleeps = []
        c = BinanceClient("https://api.binance.com", session=s, sleep=sleeps.append)
        return c, s, sleeps

    def test_success_and_url(self):
        c, s, _ = self.client([FakeResp(200, {"price": "41.5"})])
        self.assertEqual(c.price("USDTTRY"), 41.5)
        self.assertEqual(s.urls[0][0], "https://api.binance.com/api/v3/ticker/price")

    def test_retries_on_429_and_5xx_then_succeeds(self):
        c, s, sleeps = self.client([FakeResp(429, headers={"Retry-After": "2"}),
                                    FakeResp(503), FakeResp(200, {"serverTime": 123})])
        self.assertEqual(c.server_time_ms(), 123)
        self.assertEqual(len(s.urls), 3)
        self.assertEqual(sleeps[0], 2)

    def test_network_error_retries_then_raises_data_error(self):
        c, s, _ = self.client([requests.ConnectionError("x")] * 3)
        with self.assertRaises(DataError):
            c.server_time_ms()
        self.assertEqual(len(s.urls), 3)

    def test_geo_block_not_retried(self):
        c, s, _ = self.client([FakeResp(451)])
        with self.assertRaises(DataError) as cm:
            c.server_time_ms()
        self.assertEqual(len(s.urls), 1)
        self.assertIn("451", str(cm.exception))

    def test_bad_json_and_bad_price(self):
        c, _, _ = self.client([FakeResp(200, None)])
        with self.assertRaises(DataError):
            c.server_time_ms()
        c, _, _ = self.client([FakeResp(200, {"price": "0"})])
        with self.assertRaises(DataError):
            c.price("USDTTRY")
        c, _, _ = self.client([FakeResp(200, {"nope": 1})])
        with self.assertRaises(DataError):
            c.price("USDTTRY")

    def test_only_whitelisted_get_endpoints(self):
        c, s, _ = self.client([])
        for path in ("/api/v3/order", "/api/v3/account", "/sapi/v1/capital/withdraw/apply",
                     "/api/v3/order/test"):
            with self.assertRaises(ValueError):
                c._get(path)
        self.assertEqual(s.urls, [])
        self.assertEqual(ALLOWED_PATHS, {"/api/v3/time", "/api/v3/klines", "/api/v3/ticker/price"})

    def test_closed_candles_end_to_end(self):
        cs = candles_from_closes(base_closes(), END)
        forming = Candle(END, 1.0, 2.0, 1.0, 1.5, END + IV - 1)
        rows = rows_from(cs) + rows_from([forming])
        c, s, _ = self.client([FakeResp(200, {"serverTime": END + 20_000}), FakeResp(200, rows)])
        tail, server_ms = c.closed_candles(CFG)
        self.assertEqual(server_ms, END + 20_000)
        self.assertEqual(tail[-1].open_ms, cs[-1].open_ms)
        self.assertEqual(s.urls[1][1]["symbol"], "BTCTRY")


if __name__ == "__main__":
    unittest.main()
