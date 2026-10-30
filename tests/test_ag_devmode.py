#!/usr/bin/env python3
"""Part 5 rev2: daily checks (tick-driven), push-signalled relay updates, relay admin ops, stderr tails."""
import contextlib, io, json, os, shutil, subprocess, tempfile, time, unittest
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_ag_relay import Env, ag

SHA = "0123456789abcdef0123456789abcdef01234567"


class UpdateChecks(Env):
    def test_daily_interval_and_tick_runs_the_check(self):
        self.assertEqual(ag.UPDATE_EVERY_S, 86400)
        self.assertNotIn("AG_UPDATE_EVERY_S", Path(ag.__file__).read_text())
        with patch.object(ag, "_update_check_inline", return_value={"status": "current", "detail": "x"}), \
                patch.object(ag, "_sched_tick_dir", return_value={"dir": str(self.o["state"]), "actions": []}):
            args = ag.build().parse_args(["--dir", str(self.o["state"]), "--json", "schedule", "tick"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf): rc = ag.do_schedule(args)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["data"]["update"]["status"], "current")

    def test_inline_check_uses_the_daily_stamp(self):
        self.setenv(self.o); os.environ.pop("AG_AUTO_UPDATE", None)
        self.addCleanup(os.environ.__setitem__, "AG_AUTO_UPDATE", "0")
        stamp = ag._update_stamp(); stamp.parent.mkdir(parents=True, exist_ok=True); stamp.touch()
        boom = Mock(return_value=("current", "same"))
        with patch.object(ag, "self_update", boom):
            self.assertIsNone(ag._update_check_inline()); boom.assert_not_called()
            old = time.time() - ag.UPDATE_EVERY_S - 5; os.utime(stamp, (old, old))
            self.assertEqual(ag._update_check_inline()["status"], "current"); boom.assert_called_once()
        self.assertIn("current same", (ag._update_stamp().with_name("update.log")).read_text())

    def test_installed_commit_is_update_commit_for_copy_installs(self):
        self.setenv(self.o); os.environ.pop("AG_AUTO_UPDATE", None)
        self.addCleanup(os.environ.__setitem__, "AG_AUTO_UPDATE", "0")
        src = self.td / "copy" / "ag"; src.parent.mkdir(); src.write_text("# copy\n")
        ag._update_commit_write(SHA)
        with patch.object(ag, "ag_script_path", return_value=str(src)):
            self.assertEqual(ag.update_installed_commit(), SHA)
            self.assertEqual(ag._relay_commit(), SHA)
            st = ag._update_status_data()
        self.assertEqual(st["installed_commit"], SHA)
        self.assertEqual(st["interval_s"], 86400); self.assertEqual(st["mode"], "daily")

    def test_ls_remote_sha_then_pinned_download(self):
        fake = Path(self.o["env"]["PATH"].split(os.pathsep)[0]) / "git"
        fake.write_text(f'#!/bin/sh\n[ "$1" = "ls-remote" ] && {{ echo "{SHA}\\trefs/heads/master"; exit 0; }}\nexit 1\n')
        fake.chmod(0o755)
        self.setenv(self.o)
        self.assertEqual(ag._update_remote_sha(), SHA)

        src = self.td / "copy" / "ag"; src.parent.mkdir(); src.write_text("old\n")
        class Resp:
            def __init__(self, body): self.body = body
            def read(self): return self.body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with patch.object(ag, "_update_remote_sha", return_value=SHA), \
                patch.object(urllib.request, "urlopen", return_value=Resp(b"# new\n")) as urlopen:
            status, _ = ag.self_update(src)
        self.assertEqual(status, "updated")
        self.assertEqual(urlopen.call_args.args[0], f"https://raw.githubusercontent.com/Segzman/ag/{SHA}/ag")
        self.assertEqual(src.read_text(), "# new\n")
        self.assertEqual(ag._update_commit_read(), SHA)
        with patch.object(ag, "_update_remote_sha", return_value=SHA), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("must not download")):
            self.assertEqual(ag.self_update(src)[0], "current")


class Admin(Env):
    def setUp(self):
        super().setUp()
        self.init(self.o); self.init(self.t)

    def publish_admin(self, op, arg=None, target="muse"):
        self.setenv(self.o)
        rec = ag.relay_publish_admin(ag.relay_cfg(), target, op, arg)
        self.assertTrue((self.mail / "admin" / target / f'mac--{rec["id"]}.json').exists())
        return rec

    def run_target(self, ops=None, update=("updated", "old -> new")):
        self.setenv(self.t)
        cfg = ag.relay_cfg()
        if ops is not None: cfg["admin_ops"] = ops
        with patch.object(ag, "self_update", return_value=update) as upd:
            out = ag.relay_sync(cfg)
        return out, upd

    def result(self, rid):
        return json.loads((self.mail / "results" / "mac" / f'muse--{rid}--admin.json').read_text())

    def test_admin_round_trip_for_every_op(self):
        rid = self.publish_admin("update")["id"]
        out, upd = self.run_target()
        self.assertEqual(out["admin"], [rid]); upd.assert_called_once()
        res = self.result(rid)
        self.assertEqual((res["op"], res["status"], res["detail"]), ("update", "ok", "updated: old -> new"))

        nd = self.td / "notify-admin"
        rid = self.publish_admin("notify-dir", str(nd))["id"]
        out, _ = self.run_target()
        self.assertEqual(out["admin"], [rid])
        cfg = json.loads((self.t["cfg"] / "notify.json").read_text())
        self.assertEqual(cfg["dir"], str(nd)); self.assertEqual(cfg["notify_dir"], str(nd))
        self.assertEqual(self.result(rid)["status"], "ok")

        rid = self.publish_admin("status")["id"]
        out, _ = self.run_target()
        self.assertEqual(out["admin"], [rid])
        res = self.result(rid)
        self.assertEqual(res["status"], "ok")
        self.assertIn("update: off", res["detail"]); self.assertIn("relay: muse", res["detail"])

        self.sync(self.o)  # origin ingests admin results like any other
        p, d = self.j(self.o, "relay", "results", "--id", rid)
        self.assertEqual(d["data"]["results"][0]["op"], "status")
        self.assertIn("relay: muse", d["data"]["results"][0]["detail"])
        self.assertIn("status", p.stdout)

    def test_unknown_op_rejected(self):
        p = self.run_ag(self.o, "relay", "admin", "muse", "dev-on", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown admin op", p.stdout + p.stderr)

    def test_admin_ops_empty_disables_and_each_runs_once(self):
        rid = self.publish_admin("update")["id"]
        out, upd = self.run_target(ops=[])
        self.assertEqual(out["admin"], []); upd.assert_not_called()
        self.assertFalse((self.mail / "results" / "mac" / f'muse--{rid}--admin.json').exists())
        out, upd = self.run_target()
        self.assertEqual(out["admin"], [rid]); upd.assert_called_once()
        out, later = self.run_target()
        self.assertEqual(out["admin"], []); later.assert_not_called()  # processed once
        upd.assert_called_once()

    def test_error_tail_carries_backend_stderr(self):
        self.setenv(self.t)
        name = "relay-mac-w1"
        self.add_agent(self.t, name)
        sdir = self.t["state"]
        ag._sched_save(sdir, [{"id": "mac--s1", "agent": name, "relay_origin": "mac", "relay_id": "s1",
            "last_job": "job1", "last_due": 123}])
        ag._wake_write_job(sdir, {"id": "job1", "agent": name, "status": "failed", "error": "backend exit 1",
            "ts": ag.now_iso(), "ended_at": datetime.fromtimestamp(time.time() + 1).astimezone().isoformat(),
            "backend_exit": 1, "backend_stderr": "traceback line\nprovider said no"})
        out = {"reported": []}
        ag._relay_report(ag.relay_cfg(), self.mail, time.time(), out, [])
        res = json.loads((self.mail / "results" / "mac" / "muse--s1--123.json").read_text())
        self.assertEqual(res["status"], "failed")
        self.assertIn("provider said no", res["error_tail"])


class Push(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-push-t-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.bare = self.td / "remote.git"; self.repo = self.td / "repo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(self.bare)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.bare), str(self.repo)], check=True, capture_output=True)
        for a in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(self.repo), "config", *a], check=True)
        shutil.copy(Path(ag.__file__), self.repo / "ag")
        subprocess.run(["git", "-C", str(self.repo), "add", "ag"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "v1"], check=True)
        self.mail = self.td / "mail"; self.mail.mkdir()
        (self.mail / "nodes").mkdir()
        for node in ("mac", "muse"):
            (self.mail / "nodes" / f"{node}.json").write_text(json.dumps({"node": node, "last_seen": int(time.time())}))
        cfgd = self.td / "cfg"; cfgd.mkdir()
        (cfgd / "relay.json").write_text(json.dumps({"node": "mac", "transport": "dir", "url": str(self.mail),
            "checkout": "", "state": str(self.td / "state"), "workdir": str(self.td / "work"), "max_mode": "edits"}))
        self.env = {"AG_CONFIG_HOME": str(cfgd), "AG_CACHE_HOME": str(self.td / "cache"), "AG_AUTO_UPDATE": "0"}

    def test_push_signals_every_peer_except_self(self):
        args = ag.build().parse_args(["--json", "push", "origin", "master"])
        buf = io.StringIO()
        with patch.dict(os.environ, self.env), patch.object(ag, "ag_script_path", return_value=str(self.repo / "ag")), \
                contextlib.redirect_stdout(buf):
            rc = ag.do_push(args)
        self.assertEqual(rc, 0)
        d = json.loads(buf.getvalue())["data"]
        self.assertTrue(d["pushed"]); self.assertEqual(d["signalled"], ["muse"])
        self.assertNotIn("mac", d["signalled"])
        files = list((self.mail / "admin" / "muse").glob("mac--*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["op"], "update")

    def test_push_reports_remote_old_to_new(self):
        git = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True, text=True).stdout.strip()
        git("push", "-q", "-u", "origin", "master")
        v1 = git("rev-parse", "--short", "HEAD")
        (self.repo / "note.txt").write_text("v2")
        git("add", "note.txt"); git("commit", "-qm", "v2")
        v2 = git("rev-parse", "--short", "HEAD")
        args = ag.build().parse_args(["--json", "push", "origin", "master"])
        buf = io.StringIO()
        with patch.dict(os.environ, self.env), patch.object(ag, "ag_script_path", return_value=str(self.repo / "ag")), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(ag.do_push(args), 0)
        d = json.loads(buf.getvalue())["data"]
        self.assertEqual((d["old"], d["new"]), (v1, v2))


if __name__ == "__main__":
    unittest.main()
