"""Workflow ve bağımlılık yapılandırması doğrulaması (metin tabanlı, ek paket gerektirmez)."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.bot = read(".github", "workflows", "bot.yml")
        self.dog = read(".github", "workflows", "watchdog.yml")
        self.test = read(".github", "workflows", "test.yml")

    def test_only_expected_workflows_exist(self):
        names = sorted(os.listdir(os.path.join(ROOT, ".github", "workflows")))
        self.assertEqual(names, ["bot.yml", "lab.yml", "paper-b.yml", "research.yml", "test.yml", "watchdog.yml"])

    def test_bot_runs_only_tested_version(self):
        self.assertIn("workflows/test.yml/runs?branch=main&status=success", self.bot)
        self.assertIn("ref: ${{ steps.good.outputs.sha }}", self.bot)
        self.assertIn("actions: read", self.bot)
        # kapı, kod çekilmeden ve bot çalışmadan ÖNCE gelir
        i_gate = self.bot.index("id: good")
        self.assertLess(i_gate, self.bot.index("actions/checkout@v4"))
        self.assertLess(self.bot.index("actions/checkout@v4"), self.bot.index("python bot.py"))

    def test_bot_has_concurrency_timeout_and_pip_check(self):
        self.assertIn("group: paper-bot", self.bot)
        self.assertIn("cancel-in-progress: false", self.bot)
        self.assertRegex(self.bot, r"timeout-minutes: \d+")
        self.assertIn("pip check", self.bot)
        self.assertIn("pip check", self.test)

    def test_cron_expressions_are_valid_five_fields(self):
        for text in (self.bot, self.dog):
            for m in re.finditer(r"cron: '([^']+)'", text):
                self.assertEqual(len(m.group(1).split()), 5, m.group(1))

    def test_secrets_only_via_secrets_context_never_echoed(self):
        for text in (self.bot, self.dog):
            self.assertNotRegex(text, r"echo[^\n]*secrets\.")
            self.assertNotRegex(text, r"(?i)ntfy\.sh/[A-Za-z0-9_-]{6,}")
        self.assertIn("secrets.NTFY_TOPIC", self.bot)
        self.assertIn("secrets.NTFY_TOPIC", self.dog)

    def test_watchdog_is_read_only_and_runs_hourly(self):
        self.assertIn("contents: read", self.dog)
        self.assertNotIn("contents: write", self.dog)
        self.assertIn("python -m paper.watchdog", self.dog)

    def test_research_is_manual_only_and_cannot_touch_main_or_bot_state(self):
        r = read(".github", "workflows", "research.yml")
        self.assertNotIn("schedule:", r)
        self.assertIn("workflow_dispatch", r)
        self.assertIn("research:research", r)
        self.assertNotRegex(r, r"push[^\n]*(main|data)\b")
        self.assertNotIn("NTFY_TOPIC", r)
        self.assertIn("unittest tests.test_research", r)

    def test_lab_is_manual_freezes_selection_before_final_and_never_touches_bot(self):
        t = read(".github", "workflows", "lab.yml")
        self.assertNotIn("schedule:", t)
        self.assertNotIn("NTFY_TOPIC", t)
        i_sel = t.index("strategy_lab.run select")
        i_frz = t.index("SEÇİM DONDURULDU")
        i_fin = t.index("strategy_lab.run final")
        self.assertLess(i_sel, i_frz)
        self.assertLess(i_frz, i_fin)
        self.assertIn("fetch-depth: 0", t)
        self.assertNotRegex(t, r"push[^\n]*(HEAD:main|main)\b")

    def test_tests_run_on_push_but_not_for_data_branch(self):
        self.assertIn("branches-ignore: [data]", self.test)
        self.assertIn("unittest discover", self.test)

    def test_all_requirements_are_pinned(self):
        for f in ("requirements.txt", "requirements-dev.txt"):
            for line in read(f).splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-r"):
                    continue
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==\d+(\.\d+)*$", f"{f}: {line}")

    def test_no_order_or_key_code_in_package(self):
        for fn in os.listdir(os.path.join(ROOT, "paper")):
            if fn.endswith(".py"):
                src = read("paper", fn)
                for bad in ("api/v3/order", "X-MBX-APIKEY", "hmac", "requests.post(", "BINANCE_API_KEY"):
                    if fn == "binance.py" and bad == "api/v3/order":
                        continue  # yalnızca açıklama metninde geçer
                    if fn == "notify.py" and bad == "requests.post(":
                        continue
                    self.assertNotIn(bad, src, f"{fn}: {bad}")


if __name__ == "__main__":
    unittest.main()
