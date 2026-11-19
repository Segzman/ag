#!/usr/bin/env python3
"""Relay delivery warnings/UNDELIVERED, push races + stale rebases, age secrets. Stdlib, temp dirs."""
import json, os, shutil, subprocess, sys, time, unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ag_relay import AG, Env, ag


class Relay(Env):
    def hbput(self, node, **kw):
        p = self.root / "nodes" / f"{node}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        hb = {"node": node, "backends": ["echo"], "names": [], "last_seen": int(time.time())}
        hb.update(kw)
        p.write_text(json.dumps(hb))
        return hb

    def run_in(self, n, *a, input=None, env=None):
        return subprocess.run([sys.executable, AG, "--dir", str(n["state"]), *a], capture_output=True,
                              text=True, input=input, env=env or n["env"], timeout=60)

    def bindir(self, n): return Path(n["work"]).parent / "bin"


class Delivery(Relay):
    def put_mail(self, target, rid, agent="coop", message="ping", age=0, once=True):
        d = self.root / "schedules" / target
        d.mkdir(parents=True, exist_ok=True)
        m = {"origin": self.o["name"], "target": target, "id": rid, "agent": agent, "message": message,
             "at_epoch": int(time.time()) - age, "enabled": True}
        if once: m["once"] = True
        (d / f'{self.o["name"]}--{rid}.json').write_text(json.dumps(m))
        return m

    def test_wake_warns_when_target_reports_no_backends(self):
        self.init(self.o); self.init(self.t)
        self.add_agent(self.o, "w1")
        self.hbput("muse", backends=[])
        p, d = self.j(self.o, "wake", "w1", "ping", "--on", "muse")
        self.assertIn("no usable backends", d["data"]["delivery_warning"])
        p = self.run_in(self.o, "wake", "w1", "ping", "--on", "muse")
        self.assertIn("warning:", p.stdout)
        self.hbput("muse", backends=["echo"])
        p, d = self.j(self.o, "wake", "w1", "ping", "--on", "muse")
        self.assertNotIn("delivery_warning", d["data"])

    def test_wake_warns_without_heartbeat(self):
        self.init(self.o)
        self.add_agent(self.o, "w1")
        p, d = self.j(self.o, "wake", "w1", "ping", "--on", "muse")
        self.assertIn("no heartbeat from muse", d["data"]["delivery_warning"])

    def test_say_warns_when_peer_has_no_claimed_mailbox(self):
        self.init(self.o); self.init(self.t)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.hbput("muse", names=[])
        p, d = self.j(self.o, "relay", "say", "hello", "--session", "s1")
        self.assertIn("no claimed relay name", d["data"]["delivery_warning"])
        p = self.run_in(self.o, "relay", "say", "again", "--session", "s1")
        self.assertIn("warning:", p.stdout)
        self.hbput("muse", names=["coop"])
        p, d = self.j(self.o, "relay", "say", "hello", "--session", "s1")
        self.assertNotIn("delivery_warning", d["data"])
        self.hbput("muse", names=["other"])
        p, d = self.j(self.o, "relay", "say", "hello", "--session", "s1")
        self.assertIn("no mailbox named coop", d["data"]["delivery_warning"])

    def test_status_lists_old_unpicked_jobs_as_undelivered(self):
        self.init(self.o); self.init(self.t)
        self.hbput("muse", backends=[])
        self.put_mail("muse", "dead", age=20 * 60)
        self.put_mail("muse", "fresh", age=30)
        p, d = self.j(self.o, "relay", "status")
        rows = d["data"]["undelivered"]
        self.assertEqual([x["id"] for x in rows], ["dead"])
        self.assertEqual(rows[0]["reason"], "target reports no usable backends")
        self.assertIn("UNDELIVERED", p.stdout)
        self.assertIn("dead", p.stdout)
        self.assertIn("no usable backends", p.stdout)
        self.hbput("muse", backends=["echo"])
        p, d = self.j(self.o, "relay", "status")
        self.assertIn("has not reported a result", d["data"]["undelivered"][0]["reason"])
        self.hbput("muse", backends=["echo"], picked=["mac--dead"])
        p, d = self.j(self.o, "relay", "status")
        self.assertEqual(d["data"]["undelivered"], [])  # target pulled it; long-running is not undelivered
        self.hbput("muse", backends=["echo"])
        rd = self.root / "results" / "mac"
        rd.mkdir(parents=True)
        (rd / "muse--dead--1.json").write_text("{}")
        p, d = self.j(self.o, "relay", "status")
        self.assertEqual(d["data"]["undelivered"], [])
        self.assertNotIn("UNDELIVERED", p.stdout)


class PushRace(Relay):
    def fake_git(self, body):
        p = self.bindir(self.o) / "git"
        p.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GITLOG\"\n" + body)
        p.chmod(0o755)

    def cfg(self):
        co = self.td / "checkout"
        co.mkdir(parents=True, exist_ok=True)
        return {"node": "mac", "transport": "git", "checkout": str(co), "url": str(self.td / "bare"),
                "state": str(self.o["state"]), "workdir": str(self.o["work"]), "max_mode": "edits"}

    def env(self):
        log, count = self.td / "git.log", self.td / "git.count"
        return {"PATH": self.o["env"]["PATH"], "GITLOG": str(log), "COUNT": str(count)}, log, count

    def test_push_retries_cannot_lock_ref_and_fetch_first(self):
        self.fake_git('case "$*" in\n'
            '  *push*) n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNT"\n'
            '    if [ "$n" -le 2 ]; then echo "error: cannot lock ref refs/heads/master" >&2; exit 1; fi\n'
            '    if [ "$n" -eq 3 ]; then echo "! [rejected] master -> master (fetch first)" >&2; exit 1; fi\n'
            '    exit 0;;\n'
            '  *) exit 0;;\n'
            'esac\n')
        env, log, count = self.env()
        sleeps = []
        out, errs = {}, []
        with patch.dict(os.environ, env), patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
            ag._relay_push(self.cfg(), out, errs)
        self.assertTrue(out.get("pushed"), errs)
        self.assertEqual(errs, [])
        self.assertEqual(int(count.read_text()), 4)
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(all(0.2 <= s <= 4.25 for s in sleeps), sleeps)
        self.assertIn("pull --rebase", log.read_text())

    def test_push_gives_up_after_five_attempts(self):
        self.fake_git('case "$*" in *push*) n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNT"; '
            'echo "error: cannot lock ref" >&2; exit 1;; *) exit 0;; esac\n')
        env, log, count = self.env()
        sleeps = []
        out, errs = {}, []
        with patch.dict(os.environ, env), patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
            ag._relay_push(self.cfg(), out, errs)
        self.assertFalse(out.get("pushed"))
        self.assertIn("cannot lock ref", errs[0])
        self.assertEqual(int(count.read_text()), 5)
        self.assertEqual(len(sleeps), 4)

    def test_stale_rebase_is_continued_or_aborted(self):
        co = self.td / "checkout"; co.mkdir()
        (co / ".git" / "rebase-merge").mkdir(parents=True)
        self.fake_git('case "$*" in *rebase*continue*) exit 0;; *rebase*abort*) exit 1;; *) exit 0;; esac\n')
        env, log, _ = self.env()
        with patch.dict(os.environ, env):
            self.assertIsNone(ag._relay_finish_rebase({"node": "mac"}, co))
        txt = log.read_text()
        self.assertIn("rebase --continue", txt)
        self.assertNotIn("rebase --abort", txt)
        self.fake_git('case "$*" in *rebase*continue*) exit 1;; *rebase*abort*) exit 0;; *) exit 0;; esac\n')
        log.write_text("")
        with patch.dict(os.environ, env):
            self.assertIsNone(ag._relay_finish_rebase({"node": "mac"}, co))
        txt = log.read_text()
        self.assertIn("rebase --continue", txt)
        self.assertIn("rebase --abort", txt)


class Secrets(Relay):
    def fake_age(self, n):
        p = self.bindir(n) / "age"
        p.write_text("#!/bin/sh\n"
            'out=""; mode=enc; file=""\n'
            'while [ $# -gt 0 ]; do case "$1" in\n'
            '  -o) out="$2"; shift 2;;\n'
            '  -d) mode=dec; shift;;\n'
            '  -i|-r) shift 2;;\n'
            '  *) file="$1"; shift;;\n'
            'esac; done\n'
            'rot() { tr "a-zA-Z" "n-za-mN-ZA-M"; }\n'
            'if [ "$mode" = dec ]; then sed "1d" "$file" | rot; else { printf "FAKEAGE\\n"; rot; } > "$out"; fi\n')
        p.chmod(0o755)
        return p

    def hbput(self, node, **kw):
        p = self.root / "nodes" / f"{node}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        hb = {"node": node, "backends": ["echo"], "names": [], "last_seen": int(time.time())}
        hb.update(kw)
        p.write_text(json.dumps(hb))

    def test_put_reads_stdin_and_leaves_only_ciphertext(self):
        self.init(self.o)
        self.fake_age(self.o)
        self.hbput("muse", age_recipient="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST muse")
        p = self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="s3cr3t\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = self.root / "secrets" / "muse" / "TOKEN.age"
        self.assertTrue(f.is_file(), p.stdout + p.stderr)
        self.assertIn("FAKEAGE", f.read_text())
        self.assertNotIn("s3cr3t", f.read_text())
        self.assertNotIn("s3cr3t", p.stdout + p.stderr)
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        hb = json.loads((self.root / "nodes" / "muse.json").read_text())
        self.assertEqual(hb["age_recipient"], "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST muse")
        self.assertEqual(hb["backends"], ["echo"])

    def test_get_decrypts_to_stdout_and_never_logs(self):
        self.init(self.o); self.init(self.t)
        self.fake_age(self.o); self.fake_age(self.t)
        self.hbput("muse", age_recipient="ssh-ed25519 AAAA muse")
        key = self.td / "relay-key"; key.write_text("PRIVATE")
        cfg = self.cfg_of(self.t); cfg["ssh_key"] = str(key)
        (self.t["cfg"] / "relay.json").write_text(json.dumps(cfg))
        self.assertEqual(self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="s3cr3t\n").returncode, 0)
        p = self.run_in(self.t, "relay", "secret", "get", "TOKEN")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "s3cr3t")
        for root in (self.o["state"], self.o["cache"], self.t["state"], self.t["cache"]):
            for f in Path(root).rglob("*"):
                if f.is_file():
                    self.assertNotIn("s3cr3t", f.read_bytes().decode("utf-8", "replace"), str(f))

    def test_get_without_relay_key_errors(self):
        self.init(self.o); self.init(self.t)
        self.fake_age(self.t)
        self.hbput("muse", age_recipient="ssh-ed25519 AAAA muse")
        (self.root / "secrets" / "muse").mkdir(parents=True)
        (self.root / "secrets" / "muse" / "TOKEN.age").write_text("FAKEAGE\n")
        p = self.run_in(self.t, "relay", "secret", "get", "TOKEN")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no relay ssh key", p.stdout + p.stderr)

    def test_rm_removes_and_commits(self):
        self.init(self.o)
        self.fake_age(self.o)
        self.hbput("muse", age_recipient="ssh-ed25519 AAAA muse")
        self.assertEqual(self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="v\n").returncode, 0)
        f = self.root / "secrets" / "muse" / "TOKEN.age"
        self.assertTrue(f.is_file())
        p, d = self.j(self.o, "relay", "secret", "rm", "muse", "TOKEN")
        self.assertFalse(f.exists())
        self.assertEqual(d["data"]["name"], "TOKEN")
        p = self.run_in(self.o, "relay", "secret", "rm", "muse", "TOKEN")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no relay secret", p.stdout + p.stderr)

    def test_missing_age_is_a_clear_error(self):
        self.init(self.o)
        env = dict(self.o["env"], PATH="/usr/bin:/bin:/usr/sbin:/sbin")
        p = self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="v\n", env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("age not installed", p.stdout + p.stderr)

    @unittest.skipUnless(shutil.which("age") and shutil.which("ssh-keygen"), "age + ssh-keygen required")
    def test_real_age_ssh_round_trip(self):
        self.init(self.o); self.init(self.t)
        key = self.td / "id_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        self.hbput("muse", age_recipient=(key.with_suffix(".pub")).read_text().strip())
        p = self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="realsecret")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = self.root / "secrets" / "muse" / "TOKEN.age"
        self.assertNotIn(b"realsecret", f.read_bytes())
        cfg = self.cfg_of(self.t); cfg["ssh_key"] = str(key)
        (self.t["cfg"] / "relay.json").write_text(json.dumps(cfg))
        p = self.run_in(self.t, "relay", "secret", "get", "TOKEN")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "realsecret")

    def test_put_commits_secret_in_git_transport(self):
        bare = self.td / "bare"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        cfg = self.init(self.o, git=str(bare))
        self.fake_age(self.o)
        self.root = Path(cfg["checkout"])
        self.hbput("muse", age_recipient="ssh-ed25519 AAAA muse")
        p = self.run_in(self.o, "relay", "secret", "put", "muse", "TOKEN", input="v\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        co = Path(cfg["checkout"])
        tracked = subprocess.run(["git", "-C", str(co), "ls-files", "--", "secrets/muse/TOKEN.age"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(tracked, "secrets/muse/TOKEN.age")
        subject = subprocess.run(["git", "-C", str(co), "log", "-1", "--format=%s"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertTrue(subject)


if __name__ == "__main__":
    unittest.main()
