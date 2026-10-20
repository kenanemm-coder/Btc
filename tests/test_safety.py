import os
import re
import tempfile
import unittest
from unittest import mock

from paper import runner
from paper.config import ConfigError, from_env
from tests.helpers import IV, FakeClient, base_closes, buy_series, ms_at

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = [os.path.join(ROOT, "bot.py")] + [
    os.path.join(ROOT, "paper", f) for f in sorted(os.listdir(os.path.join(ROOT, "paper")))
    if f.endswith(".py")
]
FORBIDDEN = [
    r"create_order", r"createOrder", r"create_market", r"create_limit", r"place_order",
    r"new_order", r"/api/v3/order", r"/sapi/", r"withdraw", r"api_?key", r"api_?secret",
    r"HMAC", r"hmac", r"signature", r"X-MBX-APIKEY", r"\bccxt\b", r"import\s+ccxt",
]


def src(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class NoRealOrderTests(unittest.TestCase):
    def test_no_order_or_key_code_anywhere_in_bot_sources(self):
        for path in SOURCES:
            text = src(path)
            # docstring/yorumlardaki açıklamalar hariç: yalnızca kod satırlarına bak
            code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
            code = re.sub(r'""".*?"""', "", code, flags=re.S)
            for pat in FORBIDDEN:
                self.assertIsNone(re.search(pat, code), f"{os.path.basename(path)}: {pat}")

    def test_only_notifier_uses_post_and_binance_module_has_none(self):
        for path in SOURCES:
            code = src(path)
            if os.path.basename(path) == "notify.py":
                continue
            self.assertNotIn(".post(", code, os.path.basename(path))
            self.assertNotIn("requests.post", code)
            self.assertNotIn(".put(", code)
            self.assertNotIn(".delete(", code)

    def test_binance_hosts_only_public_data_paths(self):
        from paper.binance import ALLOWED_PATHS
        self.assertTrue(all(p.startswith("/api/v3/") for p in ALLOWED_PATHS))
        self.assertFalse(any("order" in p or "account" in p for p in ALLOWED_PATHS))


class MainTests(unittest.TestCase):
    def test_main_end_to_end_with_fake_client_and_no_real_network(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "s.json")
            last = ms_at(2026, 10, 20, 12, 0)
            fake = FakeClient(base_closes(), last)
            with mock.patch.object(runner, "BinanceClient", lambda url: fake), \
                 mock.patch.dict(os.environ, {"NTFY_TOPIC": ""}, clear=False):
                self.assertEqual(runner.main(["--init", "--state", state]), 0)
                self.assertTrue(os.path.exists(state))
                fake.set(buy_series(), last + IV)
                self.assertEqual(runner.main(["--state", state]), 0)
                # state olmadan --init'siz çalıştırma: portföyü sessizce açmaz, kod 2
                other = os.path.join(d, "yok.json")
                self.assertEqual(runner.main(["--state", other]), 2)
                self.assertFalse(os.path.exists(other))

    def test_second_instance_cannot_run_concurrently(self):
        from paper.state import FileLock
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "s.json")
            with FileLock(state + ".lock"):
                self.assertEqual(runner.main(["--state", state]), 3)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_documented_assumptions(self):
        cfg = from_env({})
        self.assertEqual((cfg.fee_buy, cfg.fee_sell), (0.001, 0.001))
        self.assertEqual((cfg.slippage_buy, cfg.slippage_sell), (0.0005, 0.0005))
        self.assertEqual(cfg.symbol, "BTCTRY")
        self.assertEqual(cfg.initial_usdt, 1000.0)

    def test_env_overrides_and_validation(self):
        cfg = from_env({"PAPER_FEE_BUY": "0.00075", "BINANCE_BASE_URL": "https://data-api.binance.vision/"})
        self.assertEqual(cfg.fee_buy, 0.00075)
        self.assertEqual(cfg.base_url, "https://data-api.binance.vision")
        for bad in ({"PAPER_FEE_BUY": "-1"}, {"PAPER_FEE_BUY": "abc"}, {"PAPER_REPORT_HOUR": "30"},
                    {"BINANCE_BASE_URL": "http://x"}, {"PAPER_SLIPPAGE_SELL": "nan"}):
            with self.assertRaises(ConfigError, msg=str(bad)):
                from_env(bad)


if __name__ == "__main__":
    unittest.main()
