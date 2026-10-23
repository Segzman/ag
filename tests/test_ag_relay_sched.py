#!/usr/bin/env python3
"""Schedule integration with the relay core stubbed at its public boundary."""
import contextlib, importlib.machinery, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import Mock, patch

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_relay_sched_mod", AG); _s = importlib.util.spec_from_loader("ag_relay_sched_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class RelaySchedule(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-relay-sched-")); self.home = self.td / "home"
        self.sd = self.td / "state"; self.bin = self.td / "bin"
        for d in (self.home, self.bin): d.mkdir()
        self.env = dict(os.environ, AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.home / "cfg"),
            AG_CACHE_HOME=str(self.home / "cache"), AG_AUTO_UPDATE="0", PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        self.old_env = {k: os.environ.get(k) for k in ("AG_HOME", "AG_CONFIG_HOME", "AG_CACHE_HOME", "AG_AUTO_UPDATE")}
        os.environ.update({k: self.env[k] for k in self.old_env})
        self.addCleanup(self._cleanup)
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), "agents", "add", "w1", "--backend", "echo", "--role", "sub"],
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.cfg = {"node": "mac", "transport": "dir", "url": str(self.td / "mailbox"), "state": str(self.td / "remote-state")}

    def _cleanup(self):
        for k, v in self.old_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        shutil.rmtree(self.td, ignore_errors=True)

    def call(self, *argv):
        err = io.StringIO(); out = io.StringIO()
        try: args = ag.build().parse_args(["--dir", str(self.sd), "--json", "schedule", *argv])
        except SystemExit as e: return e.code, {}
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): rc = ag.do_schedule(args)
        return rc, json.loads(out.getvalue()) if out.getvalue().strip() else {}

    def rows(self): return json.loads((self.sd / "schedules.json").read_text())

    def add_remote(self, *more):
        return self.call("add", "w1", "--every", "15m", "--id", "s1", "ping", "--on", "muse", *more)

    def test_remote_add_requires_relay_and_rejects_own_node(self):
        with patch.object(ag, "relay_cfg", return_value=None, create=True):
            rc, obj = self.add_remote()
        self.assertEqual(rc, 1)
        self.assertEqual(obj["error"], "relay not set up: ag relay init --git URL")
        with patch.object(ag, "relay_cfg", return_value=self.cfg, create=True):
            rc, obj = self.call("add", "w1", "--every", "15m", "--id", "s1", "--on", "mac", "ping")
        self.assertEqual(rc, 1)
        self.assertIn("different", obj["error"])

    def test_remote_add_stores_agent_spec_and_publishes(self):
        publish = Mock()
        with patch.object(ag, "relay_cfg", return_value=self.cfg, create=True), patch.object(ag, "relay_publish_schedule", publish, create=True):
            rc, obj = self.add_remote()
        self.assertEqual(rc, 0, obj)
        row = self.rows()[0]
        self.assertEqual((row["on"], row["sdir"], row["agent_spec"]["backend"], row["agent_spec"]["data"]),
            ("muse", str(self.sd.resolve()), "echo", "work"))
        self.assertEqual(row["agent_spec"], {"backend": "echo", "model": "default", "effort": None, "mode": None, "data": "work", "stall_s": None})
        publish.assert_called_once_with(self.cfg, row, row["agent_spec"])

    def test_remote_pause_resume_remove_republish_and_run_refuses(self):
        publish, unpublish = Mock(), Mock()
        with patch.object(ag, "relay_cfg", return_value=self.cfg, create=True), \
                patch.object(ag, "relay_publish_schedule", publish, create=True), \
                patch.object(ag, "relay_unpublish_schedule", unpublish, create=True):
            self.assertEqual(self.add_remote()[0], 0)
            rc, obj = self.call("pause", "s1"); self.assertEqual(rc, 0, obj)
            self.assertFalse(self.rows()[0]["enabled"])
            self.assertFalse(publish.call_args.args[1]["enabled"])
            rc, obj = self.call("resume", "s1"); self.assertEqual(rc, 0, obj)
            self.assertTrue(self.rows()[0]["enabled"])
            self.assertTrue(publish.call_args.args[1]["enabled"])
            rc, obj = self.call("run", "s1")
            self.assertEqual(rc, 1)
            self.assertEqual(obj["error"], "runs on muse; use its schedule or wait for the next due")
            rc, obj = self.call("rm", "s1"); self.assertEqual(rc, 0, obj)
            unpublish.assert_called_once_with(self.cfg, "muse", "s1")
        self.assertEqual(self.rows(), [])

    def test_remote_rows_are_not_fired_locally_and_list_shows_remote_status(self):
        with patch.object(ag, "relay_cfg", return_value=self.cfg, create=True), patch.object(ag, "relay_publish_schedule", Mock(), create=True):
            self.assertEqual(self.add_remote()[0], 0)
        rows = self.rows(); rows[0]["next_due"] = time.time() - 1; rows[0]["last_status"] = "remote done"
        rows.append({"id": "local", "agent": "w1", "message": "ping", "enabled": True, "every_s": 900,
            "next_due": time.time() - 1, "created": time.time() - 1, "overlap": "skip", "runs": 0})
        (self.sd / "schedules.json").write_text(json.dumps(rows))
        rc, obj = self.call("tick", "--now", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()))
        self.assertEqual(rc, 0, obj)
        self.assertEqual([x["id"] for x in obj["data"]["dirs"][0]["actions"]], ["local"])
        rc, obj = self.call("list")
        self.assertEqual(rc, 0, obj)
        self.assertIn("@muse", obj["data"]["_text"])
        self.assertIn("remote done", obj["data"]["_text"])

    def test_tick_all_syncs_before_and_after_and_reports_failures(self):
        sync = Mock(side_effect=[{"error": "pull failed"}, {"error": "push failed"}])
        with patch.object(ag, "relay_cfg", return_value=self.cfg, create=True), patch.object(ag, "relay_sync", sync, create=True):
            rc, obj = self.call("tick", "--all")
        self.assertEqual(rc, 0, obj)
        self.assertEqual(sync.call_count, 2)
        self.assertIn("pull failed", obj["data"]["relay_error"])
        self.assertIn("push failed", obj["data"]["relay_error"])


if __name__ == "__main__": unittest.main()
