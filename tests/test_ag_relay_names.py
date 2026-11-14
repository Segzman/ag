#!/usr/bin/env python3
"""ag relay names: per-session mailboxes, inbox cursors/hook output, and say. Stdlib, temp dirs."""
import importlib.util, json, time
from pathlib import Path

_T = Path(__file__).resolve().parent / "test_ag_relay.py"
_s = importlib.util.spec_from_file_location("_ag_relay_support", _T)
_sup = importlib.util.module_from_spec(_s); _s.loader.exec_module(_sup)
Env, ag = _sup.Env, _sup.ag


class Names(Env):
    def names_file(self, n):
        return n["cache"] / "relay" / "names.json"

    def names(self, n):
        return json.loads(self.names_file(n).read_text())

    def put_mail(self, mid, agent, message, at_epoch=None, origin="muse"):
        d = self.root / "schedules" / "mac"
        d.mkdir(parents=True, exist_ok=True)
        m = {"origin": origin, "target": "mac", "id": mid, "agent": agent, "message": message}
        if at_epoch is not None: m["at_epoch"] = int(at_epoch)
        (d / f"{origin}--{mid}.json").write_text(json.dumps(m))

    def test_claim_conflict_force_and_echo_agent(self):
        self.init(self.o)
        p, d = self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.assertEqual(d["data"]["session"], "s1")
        p = self.run_ag(self.o, "relay", "name", "claim", "coop", "--session", "s2", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("held by another session", p.stdout + p.stderr)
        p, d = self.j(self.o, "relay", "name", "claim", "coop", "--session", "s2", "--force")
        self.assertEqual(d["data"]["session"], "s2")
        self.assertEqual(self.names(self.o)["names"]["coop"]["session"], "s2")
        a = next(x for x in self.agents(self.o) if x["name"] == "coop")
        self.assertEqual(a["backend"], "echo")

    def test_one_name_per_session_and_release(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.j(self.o, "relay", "name", "claim", "other", "--session", "s1")
        p, d = self.j(self.o, "relay", "name", "list")
        self.assertNotIn("coop", d["data"]); self.assertIn("other", d["data"])
        self.run_ag(self.o, "relay", "name", "release", "other", "--session", "s1")
        self.assertEqual(self.names(self.o)["names"], {})
        self.j(self.o, "relay", "name", "claim", "solo", "--session", "s3")
        self.run_ag(self.o, "relay", "name", "release", "--session", "s3")
        self.assertEqual(self.names(self.o)["names"], {})

    def test_default_routing_and_addressed_filter(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.j(self.o, "relay", "name", "claim", "all", "--session", "s2", "--default")
        self.j(self.o, "relay", "name", "claim", "other", "--session", "s3")
        now = int(time.time())
        self.put_mail("a1", "coop", "hello coop", at_epoch=now + 1)
        self.put_mail("a2", "chat-coop", "hello chat", at_epoch=now + 1)
        self.put_mail("a3", "stranger", "for default", at_epoch=now + 1)
        self.put_mail("a4", "other", "for other", at_epoch=now + 1)
        self.put_mail("a5", "stranger", "too old", at_epoch=now - 100)
        p = self.run_ag(self.o, "relay", "inbox", "--name", "coop", "--peek")
        self.assertIn("- [coop] a1: hello coop", p.stdout)
        self.assertIn('reply: ag relay say "<text>"', p.stdout)
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop")
        self.assertEqual([m["id"] for m in d["data"]], ["a1", "a2"])
        p, d = self.j(self.o, "relay", "inbox", "--name", "all")
        self.assertEqual([m["id"] for m in d["data"]], ["a3"])
        p, d = self.j(self.o, "relay", "inbox", "--session", "s2")
        self.assertEqual(d["data"], [])

    def test_peek_does_not_advance_cursor(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        now = int(time.time())
        self.put_mail("b1", "coop", "one", at_epoch=now + 1)
        self.put_mail("b2", "coop", "two", at_epoch=now + 1)
        for _ in range(2):
            p, d = self.j(self.o, "relay", "inbox", "--name", "coop", "--peek")
            self.assertEqual([m["id"] for m in d["data"]], ["b1", "b2"])
            self.assertEqual(self.names(self.o)["cursors"], {})
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop")
        self.assertEqual([m["id"] for m in d["data"]], ["b1", "b2"])
        self.assertEqual(self.names(self.o)["cursors"]["coop"], ["b1", "b2"])
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop")
        self.assertEqual(d["data"], [])

    def test_hook_json_and_no_name_hint(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.put_mail("h1", "coop", "hook\nmessage", at_epoch=int(time.time()) + 1)
        p = self.run_ag(self.o, "relay", "inbox", "--name", "coop", "--hook", "claude-inject")
        obj = json.loads(p.stdout)
        self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("h1", obj["hookSpecificOutput"]["additionalContext"])
        p = self.run_ag(self.o, "relay", "inbox", "--name", "coop", "--hook", "claude-inject")
        self.assertEqual(p.stdout, "")
        self.put_mail("u1", "stranger", "nobody owns me", at_epoch=int(time.time()) + 1)
        p = self.run_ag(self.o, "relay", "inbox")
        self.assertEqual(p.returncode, 0)
        self.assertIn("unaddressed relay message", p.stdout)
        self.assertIn("ag relay name claim NAME --default", p.stdout)

    def test_empty_session_claim_resolves_and_releases(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop")
        self.put_mail("c1", "coop", "no session id", at_epoch=int(time.time()) + 1)
        p, d = self.j(self.o, "relay", "inbox")
        self.assertEqual([m["id"] for m in d["data"]], ["c1"])
        self.j(self.o, "relay", "name", "claim", "next")
        self.assertEqual(sorted(self.names(self.o)["names"]), ["next"])
        self.run_ag(self.o, "relay", "name", "release")
        self.assertEqual(self.names(self.o)["names"], {})

    def test_default_owner_suppresses_no_name_hint(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "all", "--session", "s2", "--default")
        self.put_mail("d1", "stranger", "owned by default", at_epoch=int(time.time()) + 1)
        p = self.run_ag(self.o, "relay", "inbox")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")

    def test_say_publishes_schedule_for_name(self):
        self.init(self.t); self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        p, d = self.j(self.o, "relay", "say", "hello", "there", "--name", "coop")
        rid, peer = d["data"]["id"], d["data"]["peer"]
        self.assertEqual(peer, "muse")
        f = self.root / "schedules" / peer / f'mac--{rid}.json'
        mail = json.loads(f.read_text())
        self.assertEqual(mail["agent"], "coop")
        self.assertEqual(mail["message"], "[from coop] hello there")
        self.assertEqual(mail["target"], peer); self.assertTrue(mail["once"]); self.assertTrue(mail["notify_done"])
        self.assertGreaterEqual(mail["at_epoch"], int(time.time()) - 5)
        p, d = self.j(self.o, "relay", "say", "again", "--session", "s1")
        self.assertEqual(d["data"]["peer"], "muse")
        p = self.run_ag(self.o, "relay", "say", "x", "--name", "coop", "--peer", "mac", ok=False)
        self.assertIn("different relay node", p.stdout + p.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
