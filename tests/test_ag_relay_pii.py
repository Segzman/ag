#!/usr/bin/env python3
"""Relay PII: one redaction gate for every mailbox write, blocked-push alert, doctor --redact.
Fake data only (example.test / 555 numbers / fake keys), temp dirs, real git with a fake pre-push hook."""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

AG = str(Path(__file__).resolve().parent.parent / "ag")
EMAIL = "leak@example.test"
PHONE = "+1 416 555 0199"
SECRET = "sk-ant-FAKEFAKEFAKE123456"
PII_LITERAL = "Project Bluebird"

_l = importlib.machinery.SourceFileLoader("ag_mod_relay_pii", AG)
_s = importlib.util.spec_from_loader("ag_mod_relay_pii", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


def git(cwd, *a, ok=True):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid"))
    if ok: assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-relay-pii-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.root = self.td / "mail"; self.root.mkdir()
        self.state = self.td / "state"; self.state.mkdir()
        self.work = self.td / "work"; self.work.mkdir()
        self.cache = self.td / "cache"; self.cfgdir = self.td / "cfg"
        self.cfgdir.mkdir()
        self.ui = self.td / "ui.log"
        fake_ui = self.td / "fake-ui"
        fake_ui.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "%s"\n' % ("%s", self.ui))
        fake_ui.chmod(0o755)
        self.env = dict(os.environ, AG_HOME=str(self.td / "home"), AG_CONFIG_HOME=str(self.cfgdir),
                        AG_CACHE_HOME=str(self.cache), AG_UI_CMD=str(fake_ui), AG_AUTO_UPDATE="0",
                        AG_PROBE="0", AG_HUMAN_GATE="off")
        self.env.pop("AG_PII_FILE", None)
        self.p = patch.dict(os.environ, self.env, clear=False); self.p.start()
        self.addCleanup(self.p.stop)

    def cfg(self):
        return {"node": "mac", "transport": "dir", "url": str(self.root), "checkout": "",
                "state": str(self.state), "workdir": str(self.work), "max_mode": "edits"}

    def ui_lines(self):
        return [l for l in self.ui.read_text().splitlines() if l] if self.ui.exists() else []


class Redact(Base):
    def test_field_types(self):
        out = ag.relay_redact(f'mail {EMAIL} call {PHONE} key {SECRET} now')
        self.assertNotIn(EMAIL, out); self.assertIn("[email]", out)
        self.assertNotIn(PHONE, out); self.assertIn("[phone]", out)
        self.assertNotIn(SECRET, out); self.assertIn("[secret]", out)

    def test_user_literals_from_pii_file(self):
        (self.cfgdir / "pii.txt").write_text("# never committed\n" + PII_LITERAL + "\n\nx\n")
        out = ag.relay_redact(f"talked to {PII_LITERAL} about it")
        self.assertEqual(out, "talked to [pii] about it")
        self.assertEqual(ag.relay_redact("plain 2026-11-20T12:34:56Z text"), "plain 2026-11-20T12:34:56Z text")

    def test_idempotent(self):
        txt = f'{EMAIL} {PHONE} {SECRET} {PII_LITERAL} and 2026-11-20'
        (self.cfgdir / "pii.txt").write_text(PII_LITERAL + "\n")
        once = ag.relay_redact(txt)
        self.assertEqual(ag.relay_redact(once), once)
        self.assertEqual(ag.relay_redact(ag.relay_redact(once)), once)

    def test_structured_walk_keeps_numbers(self):
        obj = {"ts": 1763650000, "reply": EMAIL, "nested": [PHONE, 7], "ok": True}
        red = ag._relay_redact(obj)
        self.assertEqual(red["ts"], 1763650000); self.assertEqual(red["nested"][1], 7); self.assertTrue(red["ok"])
        self.assertEqual(red["reply"], "[email]"); self.assertEqual(red["nested"][0], "[phone]")


class WritePaths(Base):
    def test_schedule_mail(self):
        cfg = self.cfg()
        row = {"on": "muse", "id": "s1", "agent": "w1", "message": f"ping {EMAIL} / {PHONE} / {SECRET}"}
        f = ag.relay_publish_schedule(cfg, row, {"backend": "echo"})
        txt = Path(f).read_text()
        for bad in (EMAIL, PHONE, SECRET): self.assertNotIn(bad, txt)
        self.assertIn("[email]", txt); self.assertIn("[phone]", txt); self.assertIn("[secret]", txt)

    def test_say_mail(self):
        cfg = self.cfg()
        rid = ag._relay_say_mail(cfg, "muse", "coop", f"[from coop] write to {EMAIL}", {"backend": "echo"})
        f = self.root / "schedules" / "muse" / f'mac--{rid}.json'
        self.assertNotIn(EMAIL, f.read_text()); self.assertIn("[email]", f.read_text())

    def test_alias_reply_mail(self):
        cfg = self.cfg()
        r = {"relay_alias": "coop", "relay_origin": "muse", "relay_id": "w-1"}
        j = {"ts": ag.now_iso(), "ended_at": ag.now_iso()}
        ag._relay_alias_done(cfg, r, j, {"status": "done", "reply": f"contact {EMAIL} please"}, 1000)
        pubs = list((self.root / "schedules" / "muse").glob("*.json"))
        self.assertEqual(len(pubs), 1)
        self.assertNotIn(EMAIL, pubs[0].read_text()); self.assertIn("[email]", pubs[0].read_text())

    def test_alias_result_file(self):
        cfg = self.cfg()
        m = {"origin": "muse", "id": "w-2", "message": "hi"}
        ag._relay_alias_result(cfg, self.root, m, 1000, "rejected", f"your mail {EMAIL}", "bad from")
        f = next((self.root / "results" / "muse").glob("mac--w-2--1000.json"))
        self.assertNotIn(EMAIL, f.read_text()); self.assertIn("[email]", f.read_text())

    def test_wake_result_and_error_tail(self):
        class Lock:
            def close(self): pass
        row = {"relay_origin": "muse", "relay_id": "w-3", "last_job": "j1", "last_due": 42}
        job = {"status": "done", "ts": ag.now_iso(), "ended_at": ag.now_iso(),
               "reply": f"reply to {EMAIL} and {PHONE}", "error": SECRET}
        with patch.multiple(ag, _wake_read_job=lambda s, jid: dict(job),
                            _wake_reconcile=lambda s, j: j, _sched_load=lambda s: [dict(row)],
                            _sched_save=lambda s, rows: None, _sched_flock=lambda *a, **k: Lock()):
            out, errs = {"reported": []}, []
            ag._relay_report(self.cfg(), self.root, 1000, out, errs)
        f = self.root / "results" / "muse" / "mac--w-3--42.json"
        self.assertTrue(f.is_file(), errs)
        txt = f.read_text()
        for bad in (EMAIL, PHONE, SECRET): self.assertNotIn(bad, txt)
        self.assertIn("[email]", txt); self.assertIn("[phone]", txt); self.assertIn("[secret]", txt)

    def test_feedback_copy(self):
        cfg = self.cfg()
        row = {"ts": ag.now_iso(), "text": f"crash {EMAIL} {PHONE} {SECRET}", "severity": "med",
               "agent": "w1", "node": "mac", "origin": "agent", "version": "x",
               "signature": ag.feedback_signature("crash")}
        (self.state / "feedback.jsonl").write_text(json.dumps(row) + "\n")
        out, errs = {}, []
        ag._relay_feedback(cfg, self.root, 1000, out, errs)
        txt = (self.root / "feedback" / "mac.jsonl").read_text()
        for bad in (EMAIL, PHONE, SECRET): self.assertNotIn(bad, txt)

    def test_memory_publish(self):
        cfg = self.cfg()
        ag.mem_put_local({"name": "contact", "scope": "node:mac", "visibility": "all", "writer": "mac",
                          "origin": "agent", "updated": ag.now_iso(), "body": f"write to {EMAIL}"})
        ag.mem_sync(cfg)
        f = self.root / "memory" / "contact.md"
        self.assertTrue(f.is_file())
        self.assertNotIn(EMAIL, f.read_text()); self.assertIn("[email]", f.read_text())


class PushBlocked(Base):
    """Real git + a pre-push hook that rejects commits containing the fake email."""

    HOOK = ('#!/bin/sh\nif git log origin/master..HEAD -p 2>/dev/null | grep -q "leak@example.test"; then\n'
            '  echo "relay_pii_guard: push blocked, outgoing commits contain [email]" >&2\n  exit 1\nfi\nexit 0\n')

    def setUp(self):
        super().setUp()
        self.bare = self.td / "origin.git"
        self.seed = self.td / "seed"
        self.co = self.td / "checkout"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "master", str(self.seed)], check=True)
        git(self.seed, "config", "user.email", "t@example.invalid"); git(self.seed, "config", "user.name", "t")
        (self.seed / "base.txt").write_text("base\n")
        git(self.seed, "add", "-A"); git(self.seed, "commit", "-qm", "base")
        git(self.seed, "remote", "add", "origin", str(self.bare)); git(self.seed, "push", "-q", "-u", "origin", "master")
        subprocess.run(["git", "clone", "-q", str(self.bare), str(self.co)], check=True)
        git(self.co, "config", "user.email", "t@example.invalid"); git(self.co, "config", "user.name", "t")
        hook = self.co / ".git" / "hooks" / "pre-push"
        hook.write_text(self.HOOK); hook.chmod(0o755)
        self.gitcfg = {"node": "mac", "transport": "git", "url": str(self.bare), "checkout": str(self.co),
                       "state": str(self.state), "workdir": str(self.work), "max_mode": "edits"}
        (self.cfgdir / "relay.json").write_text(json.dumps(self.gitcfg))

    def commit_pii(self):
        (self.co / "a.txt").write_text(f"owner {EMAIL}\n")
        (self.co / "b.txt").write_text("second local commit\n")
        git(self.co, "add", "a.txt"); git(self.co, "commit", "-qm", "add a")
        git(self.co, "add", "b.txt"); git(self.co, "commit", "-qm", "add b")

    def sync(self):
        with patch("time.sleep"):  # skip push backoff
            return ag.relay_sync(self.gitcfg)

    def test_blocked_push_alerts_once_and_shows_in_status(self):
        self.commit_pii()
        out = self.sync()
        self.assertIn("relay_pii_guard", out["error"] or "")
        st = json.loads((self.cache / "relay" / "push-blocked.json").read_text())
        self.assertIn("relay_pii_guard", st["reason"])
        self.assertGreaterEqual(st["commits"], 2)
        lines = self.ui_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("ag: relay push blocked", lines[0])
        self.assertIn("doctor --redact", lines[0])  # PII reason says how to fix
        evs = [json.loads(l) for l in (self.state / "events.jsonl").read_text().splitlines() if l.strip()]
        self.assertEqual([e["type"] for e in evs if e["type"] == "relay_push_blocked"], ["relay_push_blocked"])
        self.sync()  # same reason again: no second notification, same recorded block
        self.assertEqual(len(self.ui_lines()), 1)
        ag._relay_push_blocked(self.gitcfg, "separate failure: cannot lock ref")  # distinct reason notifies again
        self.assertEqual(len(self.ui_lines()), 2)
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), "relay", "status"],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PUSH BLOCKED: separate failure: cannot lock ref", p.stdout)
        self.assertRegex(p.stdout, r"\(\d+ commits waiting since \d{4}-\d{2}-\d{2} \d{2}:\d{2}\)")

    def test_doctor_redact_squashes_unpushed_and_pushes(self):
        self.commit_pii()
        self.assertIn("relay_pii_guard", self.sync()["error"] or "")
        self.assertGreaterEqual(int(git(self.co, "rev-list", "--count", "HEAD", "--not", "--remotes")), 2)
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), "relay", "doctor", "--redact"],
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("redacted", p.stdout); self.assertIn("push ok", p.stdout)
        self.assertEqual(git(self.co, "rev-list", "--count", "origin/master..HEAD"), "0")
        self.assertEqual(git(self.co, "rev-list", "--count", "master", "origin/master"), "3")  # base + squashed + post-sync
        self.assertIn("relay: mac sync (redacted)", git(self.co, "log", "--format=%s", "origin/master"))
        self.assertNotIn(EMAIL, git(self.co, "show", "HEAD:a.txt"))
        self.assertIn("[email]", git(self.co, "show", "HEAD:a.txt"))
        self.assertFalse((self.cache / "relay" / "push-blocked.json").exists())  # cleared by the successful push
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), "relay", "status"],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertNotIn("PUSH BLOCKED", p.stdout)


if __name__ == "__main__":
    unittest.main()
