"""Notifications, completion pings, systemd tick units, and relay diagnostics."""
import json
import time
import unittest
from datetime import datetime
from pathlib import Path

from tests.test_ag_relay import Env, ag


class Notify(Env):
    def setUp(self):
        super().setUp()
        self.notify = self.td / "notify-muse"
        for n in (self.o, self.t): n["env"]["AG_NOTIFY_DIR"] = str(self.notify)
        self.add_agent(self.o, "w1")

    def files(self, directory=None):
        return sorted((directory or self.notify).glob("*.json"))

    def note(self, directory=None):
        return json.loads(self.files(directory)[-1].read_text())

    def wait_job(self, n, jid):
        self.run_ag(n, "wakes", "--wait", jid, "--timeout", "45", timeout=90)

    def test_notify_dir_precedence_config_and_limits(self):
        title = "T" * 130
        body = "B" * 2010
        explicit = self.td / "explicit"
        p, d = self.j(self.o, "notify", title, body, "--dir", str(explicit))
        note = json.loads(Path(d["data"]["path"]).read_text())
        self.assertEqual(Path(d["data"]["path"]).parent, explicit)
        self.assertEqual(len(note["title"]), 120)
        self.assertTrue(note["title"].endswith("…"))
        self.assertEqual(len(note["body"]), 2000)
        self.assertTrue(note["body"].endswith("…"))
        self.assertEqual(note["source"], "ag")
        self.assertIn("ts", note)
        self.assertIn("node", note)
        self.assertFalse(list(explicit.glob("*.tmp")))

        env_dir = self.td / "env"
        self.o["env"]["AG_NOTIFY_DIR"] = str(env_dir)
        _, d = self.j(self.o, "notify", "env", "wins")
        self.assertEqual(Path(d["data"]["path"]).parent, env_dir)

        configured = self.td / "configured"
        _, d = self.j(self.o, "notify", "--set-dir", str(configured))
        cfg = json.loads((self.o["cfg"] / "notify.json").read_text())
        self.assertEqual(Path(cfg["notify_dir"]), configured)
        self.o["env"].pop("AG_NOTIFY_DIR")
        _, d = self.j(self.o, "notify", "config", "wins")
        self.assertEqual(Path(d["data"]["path"]).parent, configured)

        self.t["env"].pop("AG_NOTIFY_DIR")
        p = self.run_ag(self.t, "notify", "no inbox", "fails", ok=False)
        self.assertIn("ag notify --set-dir ~/.notify-muse", p.stdout + p.stderr)

    def test_chat_delegate_and_wake_completion_notifications(self):
        _, d = self.j(self.o, "chat", "send", "w1", "chat finished", "--notify-done", "--verify", "true")
        note = self.note()
        self.assertEqual(note["title"], "ag: w1 done")
        self.assertTrue(note["body"].startswith(d["data"]["reply"][:600]))
        self.assertIn("verify exit=0", note["body"])

        _, d = self.j(self.o, "delegate", "w1", "delegate finished", "--notify-done", "--verify", "true")
        note = self.note()
        self.assertEqual(note["title"], "ag: w1 done")
        self.assertTrue(note["body"].startswith(d["data"]["reply"][:600]))

        self.run_ag(self.o, "--json", "chat", "send", "missing", "failed turn", "--notify-done", ok=False)
        note = self.note()
        self.assertEqual(note["title"], "ag: missing failed")
        self.assertIn("no such agent", note["body"])

        _, d = self.j(self.o, "wake", "w1", "wake finished", "--notify-done", "--verify", "true")
        jid = d["data"]["job"]
        self.wait_job(self.o, jid)
        note = self.note()
        job = json.loads((self.o["state"] / "wake" / "jobs" / f"{jid}.json").read_text())
        self.assertIn(jid, note["title"])
        self.assertTrue(note["body"].startswith(job["reply"][:600]))
        self.assertGreater(len(note["body"]), 500)
        self.assertIn("verify exit=0", note["body"])

    def test_schedule_and_remote_completion_notifications(self):
        _, d = self.j(self.o, "schedule", "add", "w1", "--every", "1m", "--id", "notify-local",
            "--notify-done", "scheduled ping")
        due = d["data"]["schedule"]["next_due"]
        self.run_ag(self.o, "schedule", "tick", "--now", datetime.fromtimestamp(due + 1).isoformat())
        row = next(r for r in self.rows(self.o) if r["id"] == "notify-local")
        self.assertTrue(row["last_job"], row)
        self.wait_job(self.o, row["last_job"])
        job = json.loads((self.o["state"] / "wake" / "jobs" / f'{row["last_job"]}.json').read_text())
        self.assertTrue(job.get("notify_done"), job)
        self.assertTrue(self.files(), job)

        self.init(self.o)
        self.init(self.t)
        _, d = self.j(self.o, "schedule", "add", "w1", "--every", "1m", "--id", "notify-remote",
            "--on", "muse", "--notify-done", "remote scheduled")
        mail = self.notify_mail("mac--notify-remote")
        self.assertTrue(mail["notify_done"])
        self.sync(self.t)
        self.assertTrue(self.row(self.t, "mac--notify-remote")["notify_done"])

        _, d = self.j(self.o, "wake", "w1", "cloud finished", "--on", "muse", "--notify-done")
        rid = d["data"]["job"]
        self.assertTrue(self.notify_mail(f"mac--{rid}")["notify_done"])
        self.sync(self.t)
        row = self.row(self.t, f"mac--{rid}")
        self.run_ag(self.t, "schedule", "tick", "--now", datetime.fromtimestamp(row["next_due"] + 1).isoformat())
        row = self.row(self.t, f"mac--{rid}")
        self.wait_job(self.t, row["last_job"])
        note = self.note()
        job = json.loads((self.t["state"] / "wake" / "jobs" / f'{row["last_job"]}.json').read_text())
        self.assertEqual(note["title"], f'ag: relay-mac-w1 done [{row["last_job"]}]')
        self.assertTrue(note["body"].startswith(job["reply"][:600]))

    def notify_mail(self, name):
        return json.loads((self.mail / "schedules" / "muse" / f"{name}.json").read_text())

    def test_systemd_install_print_write_and_enable(self):
        self.o["env"]["HTTP_PROXY"] = "http://proxy.local:8080"
        _, d = self.j(self.o, "schedule", "install", "--systemd")
        service, timer = d["data"]["service"], d["data"]["timer"]
        self.assertIn("Type=oneshot", service)
        self.assertIn("KillMode=process", service)
        self.assertIn("Environment=\"HOME=", service)
        self.assertIn("Environment=\"PATH=", service)
        self.assertIn("HTTP_PROXY=http://proxy.local:8080", service)
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertIn("OnBootSec=60s", timer)

        bindir = self.o["state"].parent / "bin"
        logfile = self.td / "systemctl.log"
        fake = bindir / "systemctl"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SYSTEMCTL_LOG"\n')
        fake.chmod(0o755)
        self.o["env"]["SYSTEMCTL_LOG"] = str(logfile)
        _, d = self.j(self.o, "schedule", "install", "--systemd", "--write")
        unit = self.o["state"].parent / "home" / ".config" / "systemd" / "user"
        self.assertEqual((unit / "ag-tick.service").read_text(), service)
        self.assertEqual((unit / "ag-tick.timer").read_text(), timer)
        self.assertIn("daemon-reload", logfile.read_text())
        self.assertIn("enable --now ag-tick.timer", logfile.read_text())

    def test_relay_error_tail_uses_chat_error_rows(self):
        self.setenv(self.t)
        target_agent = "relay-mac-w1"
        self.add_agent(self.t, target_agent)
        sdir = self.t["state"]
        started = ag.now_iso()
        ended = datetime.fromtimestamp(time.time() + 2).astimezone().isoformat()
        ag.append_chat(sdir, target_agent, "error", "backend failed\nprovider detail line")
        ag._sched_save(sdir, [{"id": "mac--s1", "agent": target_agent, "relay_origin": "mac", "relay_id": "s1",
            "last_job": "job1", "last_due": 123}])
        ag._wake_write_job(sdir, {"id": "job1", "agent": target_agent, "status": "failed", "error": "failed",
            "ts": started, "ended_at": ended, "exit": 1})
        cfg = {"node": "muse", "state": str(sdir)}
        out, errs = {"reported": []}, []
        ag._relay_report(cfg, self.mail, time.time(), out, errs)
        self.assertFalse(errs)
        result = json.loads((self.mail / "results" / "mac" / "muse--s1--123.json").read_text())
        self.assertEqual(result["error_tail"], "backend failed\nprovider detail line")


if __name__ == "__main__":
    unittest.main()
