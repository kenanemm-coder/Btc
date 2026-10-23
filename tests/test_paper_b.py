"""paper_b: izolasyon, idempotans, ekleme-yalnız defter, birebir dolum eşliği ve güvenli durma testleri."""
import json
import os
import re
import tempfile
import unittest

import numpy as np

from paper.binance import DataError
from paper_b import run as PB
from strategy_lab import spec, strategies
from strategy_lab.backtest import backtest, costs_from

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR = PB.BAR_MS
T0 = 1_700_000_000_000 // BAR * BAR          # sentetik başlangıç (4s ızgarasında)
N_WARM, N_FWD = 420, 700
START = T0 + N_WARM * BAR                    # sentetik 'ileri tarihli' başlangıç


def make_series(seed=11, n=N_WARM + N_FWD):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    drift = 0.0009 * np.sin(t / 55.0)
    r = drift + rng.normal(0, 0.012, n)
    c = 2_000_000 * np.exp(np.cumsum(r))
    o = np.concatenate([[2_000_000.0], c[:-1]])
    sp = np.abs(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1 + sp)
    l = np.minimum(o, c) * (1 - sp)
    om = T0 + t * BAR
    return om, o, h, l, c


class Fake:
    def __init__(self, series, upto_bars=None):
        self.om, self.o, self.h, self.l, self.c = series
        k = len(self.om) if upto_bars is None else upto_bars
        self.server_ms = int(self.om[k - 1]) + BAR  # k. mum az önce kapandı

    def server_time_ms(self):
        return self.server_ms

    def _get(self, path, params=None):
        assert path == "/api/v3/klines", path
        a = int(np.searchsorted(self.om, params["startTime"], "left"))
        b = int(np.searchsorted(self.om, params["endTime"], "right"))
        b = min(b, a + params.get("limit", 1000))
        return [[int(self.om[i]), str(self.o[i]), str(self.h[i]), str(self.l[i]), str(self.c[i]), "1",
                 int(self.om[i] + BAR - 1)] for i in range(a, b)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "paper_b_state.json")
        self.s = make_series()

    def run_pb(self, series=None, upto=None, init=False, dry=False):
        cl = Fake(series or self.s, upto)
        return PB.run_once(self.path, "https://x", init=init, dry_run=dry, client=cl, start_open_ms=START)

    def raw(self):
        with open(self.path, "rb") as f:
            return f.read()

    @staticmethod
    def core(st):
        return {k: st[k] for k in ("trades", "equity_log", "open_position", "pending_entry", "last_bar_open_ms")}


class FrozenSettings(unittest.TestCase):
    def test_constants_match_frozen_selection(self):
        self.assertEqual(PB.COSTS, spec.COSTS["temel"])
        self.assertEqual(PB.START_OPEN_MS, 1_792_728_000_000)  # selection.json data_end = 2026-10-23 04:00 UTC
        self.assertEqual(PB.START_OPEN_MS % PB.BAR_MS, 0)
        self.assertEqual((PB.PARAMS["entry_n"], PB.PARAMS["atr_k"], PB.PARAMS["exit"]), (40, 3.0, "trail"))
        n = len(make_series()[0])
        d = dict(zip(("open_ms", "open", "high", "low", "close"), make_series()))
        self.assertEqual(strategies.make_b(d, "4h", 40, 3.0, "trail").name, "B[4h N=40 k=3.0 trail]")
        self.assertGreater(n, 0)


class Behaviour(Base):
    def test_matches_unmodified_backtest_exactly(self):
        st, info = self.run_pb(init=True)
        om, o, h, l, c = self.s
        d = {"open_ms": om, "open": o, "high": h, "low": l, "close": c}
        sg = strategies.make_b(d, "4h", 40, 3.0, "trail")
        si = int(np.searchsorted(om, START))
        ref = backtest(o, h, l, c, sg.entry, sg.exit_sig, costs_from(spec.COSTS["temel"]), sg.atr, sg.init_k,
                       sg.trail_k, si)
        self.assertGreaterEqual(len(ref["trades"]), 3, "sentetik veri işlem üretmeli")
        self.assertEqual(len(st["trades"]), len(ref["trades"]))
        for rec, t in zip(st["trades"], ref["trades"]):
            self.assertEqual(rec["entry_open_ms"], int(om[t["entry_i"]]))
            self.assertEqual(rec["exit_open_ms"], int(om[t["exit_i"]]))
            self.assertEqual(rec["ret"], float(t["ret"]))
        self.assertEqual(len(st["equity_log"]), len(om) - si)
        self.assertEqual(st["equity_log"][-1][1], float(ref["eq"][-1]))
        self.assertTrue(all(ms >= START for ms, _ in st["equity_log"]))

    def test_rerun_is_idempotent_no_duplicates(self):
        st1, _ = self.run_pb(init=True)
        before = self.core(json.loads(self.raw()))
        st2, info = self.run_pb()
        st3, info3 = self.run_pb()
        self.assertEqual(info["yeni_islem"], 0)
        self.assertEqual(info["yeni_mum"], 0)
        self.assertEqual(before, self.core(json.loads(self.raw())))
        ids = [t["id"] for t in st3["trades"]]
        self.assertEqual(len(ids), len(set(ids)))
        ms = [r[0] for r in st3["equity_log"]]
        self.assertEqual(len(ms), len(set(ms)))

    def test_incremental_runs_equal_one_shot(self):
        n = len(self.s[0])
        for k in range(N_WARM + 5, n, 53):
            self.run_pb(upto=k, init=not os.path.exists(self.path))
            self.run_pb(upto=k)  # aynı mumda çift çalışma
        self.run_pb()
        inc = self.core(json.loads(self.raw()))
        os.remove(self.path)
        self.run_pb(init=True)
        one = self.core(json.loads(self.raw()))
        self.assertEqual(inc, one)

    def test_before_start_nothing_is_recorded(self):
        st, info = self.run_pb(upto=N_WARM, init=True)  # son kapanmış mum başlangıçtan önce
        self.assertEqual(st["trades"], [])
        self.assertEqual(st["equity_log"], [])
        self.assertIsNone(st["last_bar_open_ms"])

    def test_dry_run_writes_nothing(self):
        self.run_pb(init=True, dry=True)
        self.assertFalse(os.path.exists(self.path))

    def test_missing_state_without_init_refuses(self):
        with self.assertRaises(PB.PaperBError):
            self.run_pb()
        self.assertFalse(os.path.exists(self.path))


class SafeStops(Base):
    def setUp(self):
        super().setUp()
        self.run_pb(init=True)
        self.snap = self.raw()

    def stops(self, fn):
        fn()
        with self.assertRaises(PB.PaperBError):
            self.run_pb()
        self.assertEqual(self.raw(), self.snap, "durumda durulduğunda dosya değişmemeli")

    def write(self, mod):
        st = json.loads(self.snap)
        mod(st)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        self.snap = self.raw()

    def test_tampered_trade_stops(self):
        self.stops(lambda: self.write(lambda s: s["trades"][0].__setitem__("ret", s["trades"][0]["ret"] + 0.01)))

    def test_phantom_trade_stops(self):
        def mod(s):
            t = dict(s["trades"][0]); t["id"] = "999"; s["trades"].append(t)
        self.stops(lambda: self.write(mod))

    def test_tampered_equity_stops(self):
        self.stops(lambda: self.write(lambda s: s["equity_log"][3].__setitem__(1, 1.5)))

    def test_config_mismatch_stops(self):
        self.stops(lambda: self.write(lambda s: s["config"]["params"].__setitem__("atr_k", 2.0)))

    def test_unknown_version_stops(self):
        self.stops(lambda: self.write(lambda s: s.__setitem__("version", 99)))

    def test_gap_in_candles_stops_and_never_creates_state(self):
        om, o, h, l, c = self.s
        keep = np.ones(len(om), dtype=bool)
        keep[N_WARM + 100] = False
        gap = (om[keep], o[keep], h[keep], l[keep], c[keep])
        with self.assertRaises(PB.PaperBError):
            self.run_pb(series=gap)
        self.assertEqual(self.raw(), self.snap)
        os.remove(self.path)
        with self.assertRaises(PB.PaperBError):
            self.run_pb(series=gap, init=True)
        self.assertFalse(os.path.exists(self.path))

    def test_data_error_leaves_file(self):
        class Boom(Fake):
            def _get(self, *a, **k):
                raise DataError("ağ")
        with self.assertRaises(DataError):
            PB.run_once(self.path, "https://x", client=Boom(self.s), start_open_ms=START)
        self.assertEqual(self.raw(), self.snap)


class Isolation(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
            return f.read()

    def test_code_has_no_orders_keys_notifications_or_bot_imports(self):
        src = self.read("paper_b", "run.py")
        for bad in ("api/v3/order", "X-MBX", "hmac", "requests.post", "API_KEY", "NTFY", "ntfy", "paper.runner",
                    "paper.notify", "paper.state", "paper.portfolio", "paper_state.json"):
            self.assertNotIn(bad, src, bad)
        self.assertRegex(src, r"from paper\.binance import BinanceClient, DataError")

    def test_workflow_is_isolated(self):
        w = self.read(".github", "workflows", "paper-b.yml")
        self.assertIn("group: paper-b", w)
        self.assertIn("cancel-in-progress: false", w)
        self.assertRegex(w, r"cron: '[^']+'")
        for m in re.finditer(r"cron: '([^']+)'", w):
            self.assertEqual(len(m.group(1).split()), 5)
        self.assertNotIn("NTFY", w)
        self.assertNotIn("paper-bot", w)
        self.assertNotIn("paper.runner", w)
        self.assertNotIn("bot.py", w)
        # hiçbir secret kullanılmaz; yalnızca iş başına kapsamlı github.token
        self.assertNotIn("secrets.", w)
        self.assertIn("github.token", w)
        # yalnızca data-b dalına yazılır; 'data' veya 'main' dalına push yok
        self.assertIn("HEAD:data-b", w)
        self.assertNotRegex(w, r"HEAD:(data|main)\b(?!-)")
        self.assertNotRegex(w, r"--branch data\b(?!-)")
        self.assertNotRegex(w, r"heads \"\$REMOTE\" data\b(?!-)")
        self.assertNotIn("--force", w)
        self.assertNotRegex(w, r"push\s+-f")
        self.assertIn("python -m paper_b.run", w)
        self.assertIn("pip check", w)
        self.assertEqual(len(re.findall(r"timeout-minutes: \d+", w)), 2)

    def test_state_decision_is_safe_when_github_unreachable(self):
        w = self.read(".github", "workflows", "paper-b.yml")
        self.assertIn("ls-remote --exit-code --heads", w)
        self.assertIn("\"$rc\" -eq 2", w)  # yalnızca 'dal yok' (2) ise ilk kurulum; başka hata = dur


def _heredoc(text, marker):
    import textwrap
    m = re.search(r"<<'%s'\n(.*?)\n\s*%s\n" % (marker, marker), text, re.S)
    assert m, marker
    return textwrap.dedent(m.group(1)) + "\n"


class PrivilegeSeparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, ".github", "workflows", "paper-b.yml"), encoding="utf-8") as f:
            cls.w = f.read()
        i = cls.w.index("\n  run:\n")
        j = cls.w.index("\n  save:\n")
        cls.head, cls.jrun, cls.jsave = cls.w[:i], cls.w[i:j], cls.w[j:]

    def test_write_permission_only_in_save_job(self):
        code_lines = [ln for ln in self.w.splitlines() if not ln.lstrip().startswith("#")]
        self.assertEqual(sum(1 for ln in code_lines if "contents: write" in ln), 1)
        self.assertIn("contents: write", self.jsave)
        self.assertIn("contents: read", self.head)
        self.assertIn("contents: read", self.jrun)
        self.assertNotIn("write", self.jrun.replace("writes", ""))  # run işinde hiçbir yazma izni/komutu yok
        self.assertNotIn("git push", self.jrun)
        self.assertNotIn("git commit", self.jrun)

    def test_run_job_never_persists_credentials(self):
        self.assertIn("persist-credentials: false", self.jrun)

    def test_save_job_has_no_third_party_code(self):
        self.assertNotRegex(self.jsave, r"\bpip\b")
        for bad in ("uses:", "python -m", "setup-python", "checkout@", "requirements", "paper_b.run",
                    "strategy_lab", "needs.run.steps"):
            self.assertNotIn(bad, self.jsave, bad)
        self.assertIn("needs: run", self.jsave)
        self.assertIn("HEAD:data-b", self.jsave)
        self.assertNotRegex(self.jsave, r"git push[^\n]*(--force|-f\b|--delete|:refs)")

    def test_validator_matches_code_and_rejects_bad_states(self):
        import copy
        import subprocess
        import sys
        code = _heredoc(self.jsave, "PY")
        good = PB.new_state()
        good["trades"] = [{"id": "5", "entry_open_ms": 5, "exit_open_ms": 6, "entry_ref": 1.0, "exit_ref": 1.0,
                           "ret": 0.0, "reason": "stop"}]
        good["equity_log"] = [[PB.START_OPEN_MS, 1.0], [PB.START_OPEN_MS + PB.BAR_MS, 1.01]]
        with tempfile.TemporaryDirectory() as d:
            def check(new, old=None):
                a = os.path.join(d, "new.json")
                with open(a, "w") as f:
                    json.dump(new, f)
                args = [sys.executable, "-", a]
                if old is not None:
                    b = os.path.join(d, "old.json")
                    with open(b, "w") as f:
                        json.dump(old, f)
                    args.append(b)
                return subprocess.run(args, input=code, text=True, capture_output=True).returncode
            self.assertEqual(check(good), 0, "kodun sabitleri iş akışındaki sabitlerle aynı olmalı")
            self.assertEqual(check(good, copy.deepcopy(good)), 0)
            bad = copy.deepcopy(good); bad["config"]["params"]["atr_k"] = 2.0
            self.assertNotEqual(check(bad), 0)
            bad = copy.deepcopy(good); bad["equity_log"][0][0] = PB.START_OPEN_MS - PB.BAR_MS
            self.assertNotEqual(check(bad), 0)
            bad = copy.deepcopy(good); bad["trades"].append(dict(bad["trades"][0]))
            self.assertNotEqual(check(bad), 0)
            shrunk = copy.deepcopy(good); shrunk["trades"] = []
            self.assertNotEqual(check(shrunk, good), 0)  # defterden işlem kaybolamaz
            rewritten = copy.deepcopy(good); rewritten["equity_log"][0][0] += 1
            self.assertNotEqual(check(rewritten, good), 0)

    def test_pre_push_hook_really_blocks_other_branches_deletes_and_tags(self):
        import subprocess
        hook = _heredoc(self.jsave, "HOOK")
        with tempfile.TemporaryDirectory() as d:
            def git(*a, cwd=None, check=True):
                return subprocess.run(["git", *a], cwd=cwd or work, text=True, capture_output=True, check=check)
            bare, work = os.path.join(d, "bare.git"), os.path.join(d, "work")
            subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
            os.makedirs(work)
            git("init", "-q", "-b", "data-b", cwd=work)
            git("config", "user.email", "t@t", cwd=work)
            git("config", "user.name", "t", cwd=work)
            with open(os.path.join(work, "f"), "w") as f:
                f.write("1")
            git("add", "f"); git("commit", "-qm", "c")
            hp = os.path.join(work, ".git", "hooks", "pre-push")
            with open(hp, "w") as f:
                f.write(hook)
            os.chmod(hp, 0o755)
            self.assertEqual(git("push", "-q", bare, "HEAD:data-b", check=False).returncode, 0)
            for target in ("HEAD:main", "HEAD:data", "HEAD:refs/heads/other", "HEAD:refs/tags/v1"):
                r = git("push", "-q", bare, target, check=False)
                self.assertNotEqual(r.returncode, 0, target)
            self.assertNotEqual(git("push", "-q", bare, ":data-b", check=False).returncode, 0)
            self.assertNotEqual(git("push", "-q", "--force", bare, "HEAD:main", check=False).returncode, 0)
            heads = subprocess.run(["git", "-C", bare, "for-each-ref", "--format=%(refname)"], text=True,
                                   capture_output=True).stdout.split()
            self.assertEqual(heads, ["refs/heads/data-b"])


if __name__ == "__main__":
    unittest.main()
