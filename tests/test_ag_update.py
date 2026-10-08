"""Self-update: fast-forward only, skips dirty/diverged, toggle persists, auto check is daily."""
import importlib.machinery, importlib.util, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = Path(__file__).resolve().parent.parent / "ag"

def load_ag():
    ld = importlib.machinery.SourceFileLoader("ag_update_mod", str(AG))
    spec = importlib.util.spec_from_loader("ag_update_mod", ld)
    m = importlib.util.module_from_spec(spec); ld.exec_module(m); return m

def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()

class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.ag = load_ag()
        os.environ["AG_CONFIG_HOME"] = str(self.td / "conf"); os.environ["AG_CACHE_HOME"] = str(self.td / "cache")
        os.environ.pop("AG_AUTO_UPDATE", None)
        bare, up, self.clone = self.td / "remote.git", self.td / "up", self.td / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True)
        subprocess.run(["git", "clone", "-q", str(bare), str(up)], check=True, capture_output=True)
        for d in (up,): git(d, "config", "user.email", "t@t"); git(d, "config", "user.name", "t")
        (up / "ag").write_text("v1\n"); git(up, "add", "ag"); git(up, "commit", "-qm", "v1"); git(up, "push", "-q", "origin", "master")
        subprocess.run(["git", "clone", "-q", str(bare), str(self.clone)], check=True, capture_output=True)
        git(self.clone, "config", "user.email", "t@t"); git(self.clone, "config", "user.name", "t")
        self.up = up

    def push(self, txt):
        (self.up / "ag").write_text(txt); git(self.up, "commit", "-qam", txt.strip()); git(self.up, "push", "-q", "origin", "master")

    def test_fast_forward_and_current(self):
        src = self.clone / "ag"
        self.assertEqual(self.ag.self_update(src)[0], "current")
        self.push("v2\n")
        st, detail = self.ag.self_update(src)
        self.assertEqual(st, "updated", detail); self.assertEqual(src.read_text(), "v2\n")

    def test_dirty_and_diverged_are_skipped(self):
        src = self.clone / "ag"; self.push("v2\n")
        src.write_text("local edit\n")
        self.assertEqual(self.ag.self_update(src)[0], "skipped"); self.assertEqual(src.read_text(), "local edit\n")
        git(self.clone, "commit", "-qam", "local")
        st, detail = self.ag.self_update(src)
        self.assertEqual(st, "skipped"); self.assertIn("diverged", detail)

    def test_toggle_and_daily_stamp(self):
        r = subprocess.run([sys.executable, str(AG), "update", "--auto", "off"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.ag = load_ag(); self.assertFalse(self.ag.auto_update_on())
        self.ag.maybe_auto_update("status"); self.assertFalse(self.ag._update_stamp().exists())
        subprocess.run([sys.executable, str(AG), "update", "--auto", "on"], check=True, capture_output=True, env=os.environ)
        os.environ["AG_AUTO_UPDATE"] = "0"; self.assertFalse(self.ag.auto_update_on()); os.environ.pop("AG_AUTO_UPDATE")
        self.assertTrue(self.ag.auto_update_on())
        st = self.ag._update_stamp(); st.parent.mkdir(parents=True, exist_ok=True); st.touch()
        m = st.stat().st_mtime; self.ag.maybe_auto_update("status")
        self.assertEqual(st.stat().st_mtime, m)  # checked recently -> no new check
        self.ag.maybe_auto_update("_daemon")     # internal subcommands never trigger

if __name__ == "__main__":
    unittest.main()
