"""Workflow'un kullandığı kabuk betikleri, yerel bir 'uzak' depoyla denenir (ağ yok)."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREP = os.path.join(ROOT, "scripts", "prepare_data.sh")
SAVE = os.path.join(ROOT, "scripts", "save_state.sh")
def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git/bash yok")
class DataBranchScriptTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.remote = os.path.join(self.d.name, "remote.git")
        self.work = os.path.join(self.d.name, "work")
        os.makedirs(self.work)
        self.git("init", "-q", "--bare", self.remote, cwd=self.d.name)
        self.url = "file://" + self.remote

    def tearDown(self):
        self.d.cleanup()

    def env(self, **extra):
        e = dict(os.environ, **GIT_ENV, REMOTE=self.url, PUSH_RETRY_SLEEP="0")
        e["GITHUB_OUTPUT"] = os.path.join(self.d.name, "out.txt")
        e.update(extra)
        return e

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.work, env=dict(os.environ, **GIT_ENV),
                              check=True, capture_output=True, text=True).stdout

    def prepare(self, remote=None):
        out = os.path.join(self.d.name, "out.txt")
        if os.path.exists(out):
            os.remove(out)
        r = subprocess.run(["bash", PREP], cwd=self.work, env=self.env(**({"REMOTE": remote} if remote else {})),
                           capture_output=True, text=True)
        init = None
        if os.path.exists(out):
            init = read(out).strip().split("init=", 1)[1]
        return r, init

    def save(self):
        return subprocess.run(["bash", SAVE], cwd=self.work, env=self.env(), capture_output=True, text=True)

    def remote_has_data(self):
        return subprocess.run(["git", "ls-remote", "--exit-code", "--heads", self.url, "data"],
                              capture_output=True).returncode == 0

    def write(self, name, text="x"):
        with open(os.path.join(self.work, "data", name), "w") as f:
            f.write(text)

    def test_first_run_inits_and_failed_run_creates_no_branch(self):
        r, init = self.prepare()
        self.assertEqual((r.returncode, init), (0, "--init"))
        # bot veri hatası aldı: yalnızca kilit + hata sayacı var, state yok
        self.write("paper_state.json.lock", "")
        self.write("error_notice.json", '{"data": 1}')
        self.assertEqual(self.save().returncode, 0)
        self.assertFalse(self.remote_has_data())      # <-- gerçek hatanın tekrarı olmamalı
        # sonraki çalışma yine ilk kurulum sayılır
        _, init = self.prepare()
        self.assertEqual(init, "--init")

    def test_successful_init_then_normal_runs_use_state(self):
        self.prepare()
        self.write("paper_state.json", '{"v": 1}')
        self.write("paper_state.json.lock", "")
        self.assertEqual(self.save().returncode, 0)
        self.assertTrue(self.remote_has_data())
        r, init = self.prepare()
        self.assertEqual((r.returncode, init), (0, ""))
        self.assertTrue(os.path.exists(os.path.join(self.work, "data", "paper_state.json")))
        # kilit dosyası depoya girmemiş olmalı
        self.assertNotIn("paper_state.json.lock", self.git("ls-tree", "-r", "--name-only", "HEAD", cwd=os.path.join(self.work, "data")))
        # değişiklik yoksa commit yok; değişiklik varsa push olur
        self.assertIn("değişmedi", self.save().stdout)
        self.write("paper_state.json", '{"v": 2}')
        self.assertEqual(self.save().returncode, 0)
        _, init = self.prepare()
        self.assertEqual(read(os.path.join(self.work, "data", "paper_state.json")), '{"v": 2}')

    def test_existing_branch_without_state_ever_is_treated_as_fresh(self):
        # Gerçekte yaşanan durum: dalda sadece kilit + hata sayacı commit'lenmiş
        seed = os.path.join(self.d.name, "seed")
        os.makedirs(seed)
        self.git("init", "-q", "-b", "data", cwd=seed)
        for n in ("error_notice.json", "paper_state.json.lock"):
            with open(os.path.join(seed, n), "w") as f:
                f.write("x")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-q", "-m", "state: eski", cwd=seed)
        self.git("push", "-q", self.url, "data", cwd=seed)
        r, init = self.prepare()
        self.assertEqual((r.returncode, init), (0, "--init"))

    def test_state_deleted_after_existing_stops_instead_of_reset(self):
        self.prepare()
        self.write("paper_state.json", '{"v": 1}')
        self.save()
        self.prepare()
        os.remove(os.path.join(self.work, "data", "paper_state.json"))
        self.git("add", "-A", cwd=os.path.join(self.work, "data"))
        self.git("commit", "-q", "-m", "silindi", cwd=os.path.join(self.work, "data"))
        self.git("push", "-q", "origin", "HEAD:data", cwd=os.path.join(self.work, "data"))
        r, init = self.prepare()
        self.assertEqual(r.returncode, 1)
        self.assertIsNone(init)
        self.assertIn("sıfırlanmasın", r.stdout + r.stderr)

    def test_unreachable_remote_stops_instead_of_init(self):
        r, init = self.prepare(remote="file://" + os.path.join(self.d.name, "yok.git"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(init)


if __name__ == "__main__":
    unittest.main()
