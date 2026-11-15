#!/usr/bin/env python3
"""Harness-agnostic tick: killable relay git calls, tick deadline, backend pick, `schedule health`. Stdlib, temp dirs."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class RelayCmd(unittest.TestCase):
    def test_timeout_kills_grandchildren_holding_pipes(self):
        # subprocess.run(timeout=) alone hangs here: the backgrounded sleep keeps stdout open (the stuck `git pull` case)
        t = time.time(); rc, out = ag._relay_cmd(["sh", "-c", "sleep 30 & sleep 30"], timeout=1)
        self.assertEqual(rc, 124); self.assertIn("timeout", out); self.assertLess(time.time() - t, 10)
        self.assertEqual(ag._CHILD_PGIDS, set())

    def test_git_never_prompts_and_has_speed_floor(self):
        env = ag._relay_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0"); self.assertIn("GIT_HTTP_LOW_SPEED_TIME", env)
        self.assertIn("ConnectTimeout", env["GIT_SSH_COMMAND"])


class Backend(unittest.TestCase):
    def test_pick(self):
        with mock.patch.object(ag.sys, "platform", "darwin"): self.assertEqual(ag._sched_backend(), "launchd")
        with mock.patch.object(ag.sys, "platform", "linux"), mock.patch.object(ag.shutil, "which", lambda n: None):
            self.assertEqual(ag._sched_backend(), "loop")
        with mock.patch.object(ag.sys, "platform", "linux"), mock.patch.object(ag.shutil, "which", lambda n: "/usr/bin/crontab" if n == "crontab" else None):
            self.assertEqual(ag._sched_backend(), "cron")


class Health(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-tick-t-")); self.cfg = self.td / "cfg"; self.bin = self.td / "bin"
        self.bin.mkdir()
        for n in ("launchctl", "systemctl", "crontab"):  # every backend reports "installed" so only tick freshness decides
            f = self.bin / n; f.write_text("#!/bin/sh\n[ \"$2\" = is-active ] && echo active\n[ \"$1\" = -l ] && echo 'x schedule tick --all'\nexit 0\n"); f.chmod(0o755)
        self.env = dict(os.environ, AG_HOME=str(self.td), AG_CONFIG_HOME=str(self.cfg), AG_CACHE_HOME=str(self.td / "cache"),
            AG_AUTO_UPDATE="0", PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        self.addCleanup(__import__("shutil").rmtree, self.td, True)

    def mark(self, **kw): self.cfg.mkdir(exist_ok=True); (self.cfg / "tick.json").write_text(json.dumps(kw))
    def health(self, *a):
        p = subprocess.run([sys.executable, AG, "--json", "schedule", "health", *a], capture_output=True, text=True, env=self.env, timeout=30)
        return p.returncode, json.loads(p.stdout)["data"]

    def test_fresh_tick_is_healthy(self):
        self.mark(pid=None, last_end=time.time() - 5, ok=True)
        rc, d = self.health(); self.assertEqual(rc, 0, d); self.assertTrue(d["healthy"])

    def test_stale_and_never(self):
        rc, d = self.health(); self.assertEqual(rc, 1); self.assertIn("tick never finished", d["problems"])
        self.mark(pid=None, last_end=time.time() - 900, ok=True)
        rc, d = self.health(); self.assertEqual(rc, 1); self.assertTrue(any("finished 9" in x for x in d["problems"]))

    def test_hung_tick_detected(self):
        sl = subprocess.Popen(["sleep", "60"]); self.addCleanup(sl.kill)
        self.mark(pid=sl.pid, started=time.time() - 3600, last_end=time.time() - 3600)
        rc, d = self.health(); self.assertEqual(rc, 1); self.assertTrue(any("hung" in x for x in d["problems"]))

    def test_tick_records_heartbeat(self):
        p = subprocess.run([sys.executable, AG, "schedule", "tick", "--all"], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads((self.cfg / "tick.json").read_text()); self.assertIsNone(st["pid"]); self.assertTrue(st["ok"])
        self.assertLess(time.time() - st["last_end"], 60)


if __name__ == "__main__": unittest.main()
