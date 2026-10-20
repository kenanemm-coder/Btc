import json
import os
import tempfile
import unittest

from paper import portfolio as pf
from paper.state import (FileLock, LockError, StateError, StateMissing, load_state,
                         save_state)
from tests.helpers import CFG


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

T = 1_760_000_000_000


def fresh():
    return pf.new_state(CFG, 40.0, T, T - 300_000, 3_000_000.0, "2026-10-20")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "paper_state.json")

    def tearDown(self):
        self.d.cleanup()

    def test_save_load_roundtrip_keeps_portfolio(self):
        s = fresh()
        pf.open_buy(s, CFG, 3_000_000.0, 1, T, T, "t")
        pf.close_position(s, CFG, 3_100_000.0, 2, T, T, "t")
        pf.open_buy(s, CFG, 3_050_000.0, 3, T, T, "t")
        save_state(self.p, s)
        back = load_state(self.p, CFG)
        self.assertEqual(back, json.loads(json.dumps(s)))
        self.assertEqual(len(back["trades"]), 1)
        self.assertIsNotNone(back["position"])

    def test_atomic_write_leaves_no_temp_and_keeps_old_on_failure(self):
        save_state(self.p, fresh())
        before = read(self.p)
        bad = fresh()
        bad["cash_try"] = float("nan")
        with self.assertRaises(ValueError):
            save_state(self.p, bad)
        self.assertEqual(read(self.p), before)       # eski dosya bozulmadı
        self.assertEqual(os.listdir(self.d.name), ["paper_state.json"])  # temp kalmadı

    def test_corrupt_state_raises_and_is_not_reset(self):
        with open(self.p, "w") as f:
            f.write('{"version": 1, "mode": "PAPER", ')
        with self.assertRaises(StateError):
            load_state(self.p, CFG)
        self.assertTrue(read(self.p).startswith('{"version"'))  # dokunulmadı

    def test_wrong_version_mode_symbol_rejected(self):
        for key, val in (("version", 2), ("mode", "LIVE"), ("symbol", "ETHTRY")):
            s = fresh()
            s[key] = val
            save_state(self.p, s)
            with self.assertRaises(StateError, msg=key):
                load_state(self.p, CFG)

    def test_inconsistent_position_rejected(self):
        s = fresh()
        s["btc"] = 0.5  # pozisyon yok ama BTC var
        save_state(self.p, s)
        with self.assertRaises(StateError):
            load_state(self.p, CFG)

    def test_missing_state_is_distinct_error(self):
        with self.assertRaises(StateMissing):
            load_state(self.p, CFG)

    def test_lock_allows_only_one_instance(self):
        with FileLock(self.p + ".lock"):
            with self.assertRaises(LockError):
                with FileLock(self.p + ".lock"):
                    pass
        with FileLock(self.p + ".lock"):  # bırakılınca tekrar alınabilir
            pass


if __name__ == "__main__":
    unittest.main()
