#!/usr/bin/env python3
"""ag relay alias: remote chats (muse says NAME -> mac runs a headless turn or hands it to a live session).
Stdlib, fake `claude` on PATH, temp dirs, dir transport, no network."""
import importlib.util, json, os, stat, sys, time
from pathlib import Path

_T = Path(__file__).resolve().parent / "test_ag_relay.py"
_s = importlib.util.spec_from_file_location("_ag_relay_support", _T)
_sup = importlib.util.module_from_spec(_s); _s.loader.exec_module(_sup)
Env, ag = _sup.Env, _sup.ag

FAKE_CLAUDE = """#!%s
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f: f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-sid-0001"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "FAKE-REPLY ok"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "result": "FAKE-REPLY ok", "session_id": "fake-sid-0001"}))
""" % sys.executable
FAKE_UI = "#!/bin/sh\nfor a; do last=\"$a\"; done\nprintf '%s\\n' \"$*\" >> \"$FAKE_UI_LOG\"\n"


class Alias(Env):
    def setUp(self):
        super().setUp()
        self.mac, self.muse = self.o, self.t  # mac holds the alias, muse is the remote sender
        self.work = self.td / "coop-work"; self.work.mkdir()
        self.claude_home = self.td / "claude-home"
        self.log, self.ui_log = self.td / "claude-calls.jsonl", self.td / "ui.log"
        for n in (self.mac, self.muse):
            n["env"].pop("AG_PARENT_AGENT", None)
            n["env"].update(AG_PROBE="0", CLAUDE_CONFIG_DIR=str(self.claude_home), FAKE_CLAUDE_LOG=str(self.log),
                FAKE_UI_LOG=str(self.ui_log), AG_UI_CMD=str(self.mac["work"] / "fake-ui"))
        fc = self.mac["work"].parent / "bin" / "claude"; fc.write_text(FAKE_CLAUDE); fc.chmod(fc.stat().st_mode | stat.S_IEXEC)
        fu = self.mac["work"] / "fake-ui"; fu.write_text(FAKE_UI); fu.chmod(fu.stat().st_mode | stat.S_IEXEC)

    def boot(self, *alias_args, max_mode=None):
        self.init(self.muse)
        self.init(self.mac, extra=(["--max-mode", max_mode] if max_mode else []))
        self.run_ag(self.mac, "relay", "alias", "add", "coop", "--backend", "claude", "--dir", str(self.work), *alias_args)

    def transcript(self, sid, age_s=0, cwd=None):
        d = self.claude_home / "projects" / ag._claude_slugs(cwd or self.work)[0]
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"; f.write_text("{}\n")
        os.utime(f, (time.time() - age_s,) * 2)
        return f

    def say(self, text="start work on X"):
        self.run_ag(self.muse, "relay", "name", "claim", "coop", "--session", "muse-s")
        p, d = self.j(self.muse, "relay", "say", "--peer", "mac", "--name", "coop", text)
        return d["data"]["id"]

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def audit(self):
        f = self.mac["state"] / "relay" / "alias-audit.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def run_headless(self):
        """mac: sync (materialise), tick the row, wait for the wake job, sync again (report + relay the reply)."""
        self.sync(self.mac)
        row = next(r for r in self.rows(self.mac) if r.get("relay_alias"))
        self.run_ag(self.mac, "schedule", "tick", "--now", self.iso(row["next_due"] + 1))
        row = next(r for r in self.rows(self.mac) if r.get("relay_alias"))
        self.run_ag(self.mac, "wakes", "--wait", str(row["last_job"]), "--timeout", "45", timeout=90)
        self.sync(self.mac)

    def result_status(self, rid):
        f = next((self.root / "results" / "muse").glob(f"mac--{rid}--*.json"))
        return json.loads(f.read_text())

    def test_add_list_rm(self):
        self.init(self.mac)
        p, d = self.j(self.mac, "relay", "alias", "add", "coop", "--backend", "claude", "--dir", str(self.work),
            "--mode", "edits", "--from", "muse,muse2", "--resume", "latest", "--model", "sonnet")
        self.assertEqual(d["data"]["from"], ["muse", "muse2"])
        p, d = self.j(self.mac, "relay", "alias", "list")
        row = d["data"]["aliases"][0]
        self.assertEqual((row["name"], row["mode"], row["resume"], row["model"]), ("coop", "edits", "latest", "sonnet"))
        self.assertIn("coop", self.cfg_of(self.mac)["aliases"])
        p, d = self.j(self.mac, "relay", "status")
        self.assertEqual(d["data"]["aliases"][0]["name"], "coop")
        self.assertIn("alias coop", self.run_ag(self.mac, "relay", "status").stdout)
        self.init(self.mac)  # re-init keeps aliases
        self.assertIn("coop", self.cfg_of(self.mac)["aliases"])
        self.run_ag(self.mac, "relay", "alias", "rm", "coop")
        self.assertNotIn("aliases", self.cfg_of(self.mac))
        self.assertEqual(self.run_ag(self.mac, "relay", "alias", "rm", "coop", ok=False).returncode, 1)

    def test_add_validation_and_agent_pending(self):
        self.init(self.mac)
        base = ["relay", "alias", "add", "coop", "--backend", "claude", "--dir", str(self.work)]
        self.assertIn("never allowed", (p := self.run_ag(self.mac, *base, "--mode", "full", ok=False)).stdout + p.stderr)
        self.assertIn("bad --from", (p := self.run_ag(self.mac, *base, "--from", "Bad Node", ok=False)).stdout + p.stderr)
        self.assertIn("needs --backend claude", (p := self.run_ag(self.mac, "relay", "alias", "add", "x1", "--backend", "codex",
            "--dir", str(self.work), "--resume", "latest", ok=False)).stdout + p.stderr)
        env = self.mac["env"]; self.mac["env"] = dict(env, AG_PARENT_AGENT="some-agent")
        self.assertIn("pending approval", (p := self.run_ag(self.mac, *base, ok=False)).stdout + p.stderr)  # human gate: agent call = pending `ag grant`
        self.mac["env"] = env
        self.assertEqual(self.cfg_of(self.mac).get("aliases"), None)

    def test_sender_not_in_from_rejected_and_logged(self):
        self.boot("--from", "other-node", "--mode", "edits")
        rid = self.say()
        self.sync(self.mac)
        self.assertEqual(self.calls(), [])
        self.assertFalse(any(r.get("relay_alias") for r in self.rows(self.mac)) if (self.mac["state"] / "schedules.json").exists() else False)
        ev = self.audit()
        self.assertEqual([e["event"] for e in ev], ["rejected"])
        self.assertEqual((ev[0]["origin"], ev[0]["alias"]), ("muse", "coop"))
        self.assertIn("not in alias coop --from", ev[0]["reason"])
        self.assertEqual(self.result_status(rid)["status"], "rejected")
        self.assertIn("rejected", self.ui_log.read_text())
        self.sync(self.muse)  # sender ingests the rejection
        self.assertEqual(json.loads(next((self.muse["cache"]).glob("relay-ingested.json")).read_text()).popitem()[1]["status"], "rejected")
        self.sync(self.mac)  # and the same mail is never gated twice
        self.assertEqual(len(self.audit()), 1)

    def test_default_from_is_nobody(self):
        self.boot()
        self.say(); self.sync(self.mac)
        self.assertEqual([e["event"] for e in self.audit()], ["rejected"])

    def test_active_claim_gets_inbox_and_no_headless(self):
        self.boot("--from", "muse", "--mode", "edits")
        self.transcript("sess-live-0001", age_s=30)
        self.run_ag(self.mac, "relay", "name", "claim", "coop", "--session", "sess-live-0001")
        rid = self.say("ping the interactive one")
        self.sync(self.mac)
        self.assertEqual(self.calls(), [])
        self.assertEqual([e["event"] for e in self.audit()], ["inbox"])
        self.assertEqual(self.result_status(rid)["status"], "inbox")
        p, d = self.j(self.mac, "relay", "inbox", "--name", "coop", "--peek")
        self.assertIn("ping the interactive one", json.dumps(d))
        self.assertIn("remote message for coop", self.ui_log.read_text())
        self.assertEqual([r for r in self.rows(self.mac) if r.get("relay_alias")] if (self.mac["state"] / "schedules.json").exists() else [], [])

    def test_dead_claim_pid_is_idle(self):
        self.boot("--from", "muse")
        self.transcript("sess-live-0002", age_s=5)
        self.run_ag(self.mac, "relay", "name", "claim", "coop", "--session", "sess-live-0002", "--pid", "999999")
        self.say(); self.sync(self.mac)
        self.assertEqual([e["event"] for e in self.audit()], ["dispatch"])

    def test_idle_claim_runs_headless_with_header_and_replies(self):
        self.boot("--from", "muse", "--mode", "edits", "--resume", "latest")
        self.transcript("sess-old-0001", age_s=7200)  # idle: last activity 2h ago
        self.run_ag(self.mac, "relay", "name", "claim", "coop", "--session", "sess-old-0001")
        rid = self.say("start work on X")
        self.run_headless()
        (call,) = self.calls()
        self.assertEqual(call["argv"][call["argv"].index("--resume") + 1], "sess-old-0001")
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.work))
        self.assertIn("[remote chat from muse (agent origin) via relay alias coop] start work on X", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(self.result_status(rid)["status"], "done")
        self.assertIn("FAKE-REPLY ok", self.result_status(rid)["reply"])
        ev = [e["event"] for e in self.audit()]
        self.assertEqual(ev, ["dispatch", "done"])
        self.assertEqual(self.audit()[1]["status"], "done")
        # reply is mail for the sender's mailbox
        self.sync(self.muse)
        p, d = self.j(self.muse, "relay", "inbox", "--name", "coop", "--peek")
        self.assertIn("[from coop@mac] FAKE-REPLY ok", json.dumps(d))
        out = self.ui_log.read_text()
        self.assertIn("remote chat coop started", out); self.assertIn("remote chat coop done", out)
        p, d = self.j(self.mac, "relay", "status")
        last = d["data"]["aliases"][0]["last"]
        self.assertEqual((last["origin"], last["path"], last["status"]), ("muse", "headless", "done"))

    def test_second_message_while_unreported_is_not_taken_as_interactive(self):
        self.boot("--from", "muse", "--resume", "latest")
        self.transcript("sess-old-0003", age_s=3)  # recent writes, but not from a live claim: no claim at all
        self.say("one"); self.sync(self.mac)
        self.say("two"); self.sync(self.mac)
        self.assertEqual([e["event"] for e in self.audit()], ["dispatch", "dispatch"])

    def test_resume_latest_picks_newest_jsonl(self):
        self.transcript("sess-older-0001", age_s=500); self.transcript("sess-newest-01", age_s=10); self.transcript("sess-oldest-01", age_s=9000)
        self.transcript("sess-elsewhere1", age_s=1, cwd=self.td)  # other dir: never picked
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_home)
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.assertEqual(ag._claude_latest_sid(self.work), "sess-newest-01")
        self.assertEqual(ag._claude_latest_sid(self.td / "none"), "")
        self.boot("--from", "muse", "--resume", "latest")
        self.say(); self.run_headless()
        (call,) = self.calls()
        self.assertEqual(call["argv"][call["argv"].index("--resume") + 1], "sess-newest-01")

    def test_resume_none_uses_dedicated_agent_without_resume(self):
        self.boot("--from", "muse", "--model", "sonnet")
        self.say(); self.run_headless()
        (call,) = self.calls()
        self.assertNotIn("--resume", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--model") + 1], "sonnet")
        a = next(x for x in self.agents(self.mac) if x["name"] == "alias-coop")
        self.assertEqual((a["backend"], a["mode"], os.path.realpath(a["dir"])), ("claude", "ro", os.path.realpath(self.work)))
        self.assertEqual(call["argv"][call["argv"].index("--permission-mode") + 1], "dontAsk")  # ro

    def test_mode_capped_by_max_mode_and_full_refused(self):
        self.boot("--from", "muse", "--mode", "auto")  # node max_mode is edits
        p, d = self.j(self.mac, "relay", "alias", "list")
        self.assertEqual((d["data"]["aliases"][0]["mode"], d["data"]["aliases"][0]["effective_mode"]), ("auto", "edits"))
        self.say(); self.sync(self.mac)
        a = next(x for x in self.agents(self.mac) if x["name"] == "alias-coop")
        self.assertEqual(a["mode"], "edits")
        self.assertEqual(self.audit()[0]["mode"], "edits")
        self.assertEqual(ag._relay_alias_mode({"max_mode": "full"}, "full"), "auto")  # hand-edited full is still never granted
        self.assertEqual(ag._relay_alias_mode({"max_mode": "ro"}, "edits"), "ro")
        self.assertEqual(ag._relay_alias_mode({"max_mode": "edits"}, "edits"), "edits")
        self.assertEqual(ag._relay_alias_mode({"max_mode": "edits"}, None), "ro")


if __name__ == "__main__":
    import unittest; unittest.main()
