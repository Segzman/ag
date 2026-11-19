#!/usr/bin/env python3
"""ag mem: shared memory store, origin/ACL rules, secret refusal, relay sync, digest.
Stdlib, temp dirs, dir-transport relay, no network, no real backends."""
import json
from pathlib import Path

_T = Path(__file__).resolve().parent / "test_ag_relay.py"
import importlib.util
_s = importlib.util.spec_from_file_location("_ag_relay_support_mem", _T)
_sup = importlib.util.module_from_spec(_s); _s.loader.exec_module(_sup)
Env, ag = _sup.Env, _sup.ag


class Mem(Env):
    def memdir(self, n): return n["cfg"] / "memory"

    def add(self, n, name, text, *extra, ok=True):
        return self.run_ag(n, "mem", "add", name, text, *extra, ok=ok)

    def policy(self, data):
        d = self.root / "admin"; d.mkdir(parents=True, exist_ok=True)
        (d / "memory-policy.json").write_text(json.dumps(data))

    def test_add_get_list_search_rm(self):
        self.init(self.o)
        self.add(self.o, "proj-note", "uses pytest and stdlib only", "--scope", "project:ag")
        f = self.memdir(self.o) / "proj-note.md"
        self.assertTrue(f.exists())
        txt = f.read_text()
        for k in ("name: proj-note", "scope: project:ag", "visibility: all", "writer: mac", "origin: agent", "updated:"):
            self.assertIn(k, txt)
        p, d = self.j(self.o, "mem", "get", "proj-note")
        self.assertIn("uses pytest", d["data"]["body"])
        p, d = self.j(self.o, "mem", "list")
        self.assertEqual([e["name"] for e in d["data"]["entries"]], ["proj-note"])
        p, d = self.j(self.o, "mem", "list", "--scope", "project:other")
        self.assertEqual(d["data"]["entries"], [])
        p, d = self.j(self.o, "mem", "search", "PYTEST")
        self.assertEqual([e["name"] for e in d["data"]["entries"]], ["proj-note"])
        p, d = self.j(self.o, "mem", "search", "nomatch")
        self.assertEqual(d["data"]["entries"], [])
        self.run_ag(self.o, "mem", "rm", "proj-note")
        self.assertFalse(f.exists())
        p = self.run_ag(self.o, "mem", "get", "proj-note", ok=False)
        self.assertEqual(p.returncode, 1)

    def test_bad_name_scope_visibility(self):
        self.init(self.o)
        for args in (("Bad Name", "x"), ("ok", "x", "--scope", "weird"), ("ok", "x", "--visibility", "somewhere")):
            p = self.run_ag(self.o, "mem", "add", *args, ok=False)
            self.assertEqual(p.returncode, 1, args)

    def test_agent_origin_cannot_write_global(self):
        self.init(self.o)  # subprocess stdin is a pipe => origin detects as agent
        p = self.add(self.o, "g1", "global fact", ok=False)
        self.assertEqual(p.returncode, 3)
        self.assertIn("global needs human origin", p.stdout + p.stderr)
        self.assertFalse((self.memdir(self.o) / "g1.md").exists())
        log = (self.memdir(self.o) / "violations.log").read_text()
        self.assertIn('"kind": "origin"', log); self.assertIn('"name": "g1"', log)
        p = self.run_ag(self.o, "mem", "add", "g2", "x", "--origin", "human", ok=False)
        self.assertEqual(p.returncode, 3)  # --origin human is not honoured for a non-human caller

    def test_human_origin_global_unit(self):
        self.setenv(self.o); self.init(self.o)
        e = {"name": "g1", "scope": "global", "visibility": "all", "writer": "mac",
             "origin": "human", "updated": "2026-01-01T00:00:00+00:00", "body": "human fact"}
        self.assertEqual(ag.mem_check_write(e, {}), (None, False))
        ag.mem_put_local(e)
        self.assertEqual(ag.mem_load()[0]["body"], "human fact")
        self.assertEqual(ag.mem_detect_origin({"AG_JOB_ID": "7"}), "agent")
        self.assertEqual(ag.mem_detect_origin({}), "agent")  # pytest stdin is not a TTY

    def test_secret_refused(self):
        self.init(self.o)
        p = self.add(self.o, "leak", "key is sk-abcdefghij0123456789", "--scope", "project:ag", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("never contain secrets", p.stdout + p.stderr)
        p = self.add(self.o, "leak2", "api_key: hunter2hunter2", "--scope", "project:ag", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(list(self.memdir(self.o).glob("*.md")), [])

    def test_sync_roundtrip_and_visibility(self):
        self.init(self.o); self.init(self.t)
        self.add(self.o, "shared", "for everyone", "--scope", "project:ag")
        self.add(self.o, "mac-only", "just mac", "--scope", "project:ag", "--visibility", "nodes:mac")
        p, d = self.j(self.o, "mem", "sync")
        self.assertEqual(sorted(d["data"]["published"]), ["mac-only", "shared"])
        self.assertTrue((self.root / "memory" / "shared.md").exists())
        p, d = self.j(self.t, "mem", "sync")
        self.assertEqual(d["data"]["materialized"], ["shared"])  # nodes:mac is not visible to muse
        self.assertTrue((self.memdir(self.t) / "shared.md").exists())
        self.assertFalse((self.memdir(self.t) / "mac-only.md").exists())
        p, d = self.j(self.t, "mem", "get", "shared")
        self.assertEqual(d["data"]["writer"], "mac")

    def test_relay_sync_materializes(self):
        self.init(self.o); self.init(self.t)
        self.add(self.o, "via-relay", "piggyback", "--scope", "project:ag")
        self.j(self.o, "mem", "sync")
        self.sync(self.t)  # plain relay sync also materialises memory/
        self.assertTrue((self.memdir(self.t) / "via-relay.md").exists())

    def test_acl_policy_blocks_add_and_sync(self):
        self.init(self.o); self.init(self.t)
        self.policy({"nodes": {"mac": {"memory.write": ["node:mac"]}}})
        p = self.add(self.o, "blocked", "nope", "--scope", "project:ag", ok=False)
        self.assertEqual(p.returncode, 3)
        self.assertIn("memory policy", p.stdout + p.stderr)
        self.assertIn('"kind": "acl"', (self.memdir(self.o) / "violations.log").read_text())
        # a policy-violating entry planted in the mailbox is not materialised by the peer
        f = self.root / "memory" / "planted.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(ag.mem_render({"name": "planted", "scope": "project:ag", "visibility": "all",
                                    "writer": "mac", "origin": "agent", "updated": "2026-01-01T00:00:00+00:00", "body": "planted"}))
        p, d = self.j(self.t, "mem", "sync")
        self.assertNotIn("planted", d["data"]["materialized"])
        self.assertFalse((self.memdir(self.t) / "planted.md").exists())
        self.assertIn('"kind": "sync-in"', (self.memdir(self.t) / "violations.log").read_text())
        # node:mac is allowed for mac
        self.add(self.o, "own", "mine", "--scope", "node:mac")

    def test_secret_in_mailbox_never_materializes(self):
        self.init(self.t)
        f = self.root / "memory" / "evil.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(ag.mem_render({"name": "evil", "scope": "project:ag", "visibility": "all",
                                    "writer": "mac", "origin": "agent", "updated": "2026-01-01T00:00:00+00:00",
                                    "body": "token sk-abcdefghij0123456789"}))
        p, d = self.j(self.t, "mem", "sync")
        self.assertNotIn("evil", d["data"]["materialized"])
        self.assertFalse((self.memdir(self.t) / "evil.md").exists())

    def test_context_digest(self):
        self.setenv(self.o); self.init(self.o)
        for i in range(30):
            ag.mem_put_local({"name": f"n{i:02}", "scope": "project:ag", "visibility": "all", "writer": "mac",
                              "origin": "agent", "updated": "2026-01-01T00:00:00+00:00",
                              "body": "line " + "x" * 100})
        ag.mem_put_local({"name": "other-proj", "scope": "project:zz", "visibility": "all", "writer": "mac",
                          "origin": "agent", "updated": "2026-01-01T00:00:00+00:00", "body": "not included"})
        dg = ag.mem_context(project="ag")
        self.assertLessEqual(len(dg), 1500)
        self.assertIn("[project:ag] n00", dg)
        self.assertNotIn("other-proj", dg)
        self.assertEqual(ag.mem_context(project="ag", node="muse"), dg)  # visibility all
        p, d = self.j(self.o, "mem", "context", "--project", "ag")
        self.assertLessEqual(len(d["data"]["digest"]), 1500)


if __name__ == "__main__":
    import unittest
    unittest.main()
