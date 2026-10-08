#!/usr/bin/env python3
"""ACP transport: fake ACP agent (python, named gemini/opencode on PATH) driven by FAKE_MODE.
Every message the fake receives lands in calls.jsonl. Run: python3 tests/test_ag_acp.py"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "normal"); log = os.environ["FAKE_LOG"]
def rec(o):
    with open(log, "a") as f: f.write(json.dumps(o) + "\n")
if "--acp" not in sys.argv and "acp" not in sys.argv[1:2]:  # plain CLI path (fallback target)
    rec({"cli": sys.argv[1:]})
    if os.path.basename(sys.argv[0]) == "opencode": print(json.dumps({"type": "text", "sessionID": "ses_cli1", "part": {"text": "cli-ok"}}))
    else: print(json.dumps({"type": "message", "role": "assistant", "content": "cli-ok"}))
    sys.exit(0)
rec({"acp": sys.argv[1:]})
if mode == "crash": sys.exit(3)
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def upd(sid, u): out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
def chunk(sid, t, kind="agent_message_chunk", mid=None):
    u = {"sessionUpdate": kind, "content": {"type": "text", "text": t}}
    if mid: u["messageId"] = mid
    upd(sid, u)
def ask(i, method, params):
    out({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
    while True:
        m = json.loads(sys.stdin.readline()); rec(m)
        if m.get("id") == i and "method" not in m: return m
print("some banner, not json"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line); rec(m); meth, i, p = m.get("method"), m.get("id"), m.get("params") or {}
    if meth == "initialize":
        out({"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": mode != "noload"}}})
    elif meth == "session/new":
        if mode == "auth": out({"jsonrpc": "2.0", "id": i, "error": {"code": -32000, "message": "Authentication required"}}); continue
        out({"jsonrpc": "2.0", "id": i, "result": {"sessionId": "s-new", "configOptions": [{"id": "model", "category": "model", "type": "select",
            "currentValue": "m/a", "options": [{"value": "m/a", "name": "A"}, {"group": "g", "options": [{"value": "m/b", "name": "B"}]}]}]}})
    elif meth == "session/load":
        if mode == "loadfail": out({"jsonrpc": "2.0", "id": i, "error": {"code": -32603, "message": "Internal error"}}); continue
        upd(p["sessionId"], {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "old q"}})
        chunk(p["sessionId"], "OLD-REPLAY")
        out({"jsonrpc": "2.0", "id": i, "result": {}})
    elif meth == "session/set_config_option":
        out({"jsonrpc": "2.0", "id": i, "result": {"configOptions": []}})
    elif meth == "session/prompt":
        sid = p["sessionId"]
        if mode == "hang":
            for line in sys.stdin:
                rec(json.loads(line))
            sys.exit(0)
        if mode == "perm":
            for n, tc in enumerate(json.loads(os.environ["FAKE_PERMS"])):
                ask(900 + n, "session/request_permission", {"sessionId": sid, "toolCall": dict(tc, toolCallId=f"p{n}"),
                    "options": [{"optionId": "aa", "kind": "allow_always", "name": "Always"}, {"optionId": "ao", "kind": "allow_once", "name": "Once"},
                                {"optionId": "ro", "kind": "reject_once", "name": "No"}, {"optionId": "ra", "kind": "reject_always", "name": "Never"}]})
        if mode == "fs":
            ask(800, "fs/read_text_file", {"sessionId": sid, "path": "/etc/hosts"})
            ask(801, "terminal/create", {"sessionId": sid, "command": "ls"})
        chunk(sid, "thinking...", "agent_thought_chunk")
        upd(sid, {"sessionUpdate": "plan", "entries": []})
        chunk(sid, "Hel", mid="m1"); chunk(sid, "lo", mid="m1")
        upd(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "ls -la", "kind": "execute", "status": "pending", "rawInput": {"command": "ls -la"}})
        upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "file-a\nfile-b"}}]})
        upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "file-a\nfile-b"}}]})
        chunk(sid, " after", mid="m2")
        upd(sid, {"sessionUpdate": "usage_update", "used": 1234, "size": 100000})
        out({"jsonrpc": "2.0", "id": i, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 3, "cachedReadTokens": 5, "totalTokens": 18}}})
    elif "id" in m and meth:
        out({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "nope"}})
'''

class Acp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); td = self.td
        self.bin = Path(td, "bin"); self.bin.mkdir()
        for b in ("gemini", "opencode"):
            f = self.bin / b; f.write_text(FAKE); f.chmod(0o755)
        self.log = Path(td, "calls.jsonl"); self.sd = Path(td, ".agent"); self.sd.mkdir()
        self.w = Path(td, "w"); self.w.mkdir()
        self.old = dict(os.environ)
        os.environ.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_LOG=str(self.log), AG_AUTO_UPDATE="0", FAKE_MODE="normal",
            AG_HOME=td+"/h", AG_CONFIG_HOME=td+"/c", AG_CACHE_HOME=td+"/k", AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0", AG_CHECKPOINT="0")
        self.env = dict(os.environ)
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
    def cli(self, *a, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=self.env)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p
    def add(self, name="g", backend="gemini", *extra):
        self.cli("agents", "add", name, "--backend", backend, "--dir", str(self.w), *(["--transport", "acp"] if backend != "acp" else []), *extra)
    def calls(self): return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []
    def methods(self): return [c.get("method") for c in self.calls() if c.get("method")]
    def rows(self, name="g"): return ag.load_chat_log(self.sd, name)

    def test_normal_stream(self):
        self.add()
        r = ag.run_turn(self.sd, "g", "hello")
        self.assertEqual(r["exit"], 0, r); self.assertEqual(r["sid"], "s-new")
        self.assertEqual(r["reply"], "Hello\n after")
        self.assertEqual(self.methods()[:3], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(self.calls()[0]["acp"], ["--acp"])
        roles = [(x["role"], x["text"]) for x in self.rows()]
        self.assertIn(("assistant", "Hello"), roles); self.assertIn(("assistant", "after"), [(a, t.strip()) for a, t in roles])
        self.assertIn(("reasoning", "thinking..."), roles)
        self.assertEqual(sum(1 for a, t in roles if a == "toolout"), 1)
        self.assertTrue(any(a == "tool" and t.startswith("execute ls -la") for a, t in roles), roles)
        u = ag.load_chat_meta(self.sd, "g")["usage"]["last"]
        self.assertEqual((u["in"], u["out"], u["cache_read"], u["ctx"]), (15, 3, 5, 1234))
        p = self.calls()[[c.get("method") for c in self.calls()].index("session/prompt")]["params"]
        self.assertEqual(p["prompt"], [{"type": "text", "text": "hello"}])

    def test_load_replay_not_duplicated(self):
        self.add()
        ag.save_chat_meta(self.sd, "g", {"sid": "s-old", "sids": {"gemini": "s-old"}, "backend": "gemini"})
        r = ag.run_turn(self.sd, "g", "again")
        self.assertEqual(r["exit"], 0, r); self.assertNotIn("OLD-REPLAY", r["reply"])
        self.assertFalse(any("OLD-REPLAY" in x["text"] for x in self.rows()))
        self.assertEqual(self.methods()[:3], ["initialize", "session/load", "session/prompt"])
        self.assertEqual(self.calls()[2]["params"]["sessionId"], "s-old")
        self.assertEqual(self.calls()[3]["params"]["prompt"][0]["text"], "again")  # resumed: no history replay

    def test_load_missing_recovers_fresh(self):
        for mode in ("noload", "loadfail"):
            self.log.write_text(""); os.environ["FAKE_MODE"] = mode
            n = "g" + mode; self.add(n)
            ag.append_chat(self.sd, n, "user", "earlier q"); ag.append_chat(self.sd, n, "agent", "earlier a")
            ag.save_chat_meta(self.sd, n, {"sid": "s-gone", "sids": {"gemini": "s-gone"}, "backend": "gemini"})
            r = ag.run_turn(self.sd, n, "now")
            self.assertEqual(r["exit"], 0, r); self.assertEqual(r["sid"], "s-new")
            self.assertIn("session/new", self.methods())
            self.assertTrue(any("[resume] session s-gone not found" in x["text"] for x in self.rows(n)))
            pr = [c for c in self.calls() if c.get("method") == "session/prompt"][-1]["params"]["prompt"][0]["text"]
            self.assertIn("earlier q", pr); self.assertTrue(pr.endswith("now"))

    def perm(self, mode, tcs):
        os.environ["FAKE_MODE"] = "perm"; os.environ["FAKE_PERMS"] = json.dumps(tcs); self.log.write_text("")
        n = f"p{mode}"
        if not any(a["name"] == n for a in ag.load_agents(self.sd)): self.add(n, "gemini", "--mode", mode)
        r = ag.run_turn(self.sd, n, "x"); self.assertEqual(r["exit"], 0, r)
        got = {c["id"]: c["result"]["outcome"] for c in self.calls() if isinstance(c.get("id"), int) and c["id"] >= 900 and "result" in c}
        return [got[900 + k].get("optionId", got[900 + k]["outcome"]) for k in range(len(tcs))], self.rows(n)

    def test_permissions(self):
        os.symlink("/", self.w / "escape")
        E = lambda p: {"kind": "edit", "title": "Write " + p, "locations": [{"path": p}]}
        tcs = [E(str(self.w / "new.txt")), E("rel/new.txt"), E("/etc/passwd"), E(str(self.w / "escape/etc/x")),
               {"kind": "read", "title": "Read"}, {"kind": "execute", "title": "rm -rf"}, {"kind": "edit", "title": "no locations"}]
        ids, rows = self.perm("edits", tcs)
        self.assertEqual(ids, ["ao", "ao", "ro", "ro", "ao", "ro", "ro"])
        self.assertTrue(any(x["role"] == "tool" and x["text"].startswith("[acp] denied edit Write /etc/passwd (mode=edits)") for x in rows), rows)
        self.assertEqual(self.perm("ro", tcs[:1] + tcs[4:6])[0], ["ro", "ao", "ro"])
        self.assertEqual(self.perm("auto", tcs[5:6] + tcs[2:3])[0], ["ao", "ro"])
        self.assertEqual(self.perm("full", tcs[2:3])[0], ["ao"])

    def test_fs_and_terminal_rejected(self):
        os.environ["FAKE_MODE"] = "fs"; self.add()
        r = ag.run_turn(self.sd, "g", "x"); self.assertEqual(r["exit"], 0, r)
        errs = {c["id"]: c["error"]["code"] for c in self.calls() if c.get("id") in (800, 801) and "error" in c}
        self.assertEqual(errs, {800: -32601, 801: -32601})
        init = self.calls()[1]["params"]["clientCapabilities"]
        self.assertEqual(init, {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False})

    def test_cancel_on_timeout(self):
        os.environ["FAKE_MODE"] = "hang"; self.add()
        t = time.monotonic(); r = ag.run_turn(self.sd, "g", "x", timeout_s=2)
        self.assertEqual(r["exit"], 124, r); self.assertLess(time.monotonic() - t, 10)
        self.assertIn("session/cancel", self.methods())
        c = [c for c in self.calls() if c.get("method") == "session/cancel"][0]
        self.assertNotIn("id", c); self.assertEqual(c["params"], {"sessionId": "s-new"})

    def test_crash_before_initialize_falls_back_to_cli(self):
        os.environ["FAKE_MODE"] = "crash"; self.add()
        r = ag.run_turn(self.sd, "g", "x")
        self.assertEqual(r["exit"], 0, r); self.assertEqual(r["reply"], "cli-ok")
        cl = [c["cli"] for c in self.calls() if "cli" in c]
        self.assertEqual(len(cl), 1); self.assertEqual(cl[0][:2], ["-p", "x"])
        self.assertTrue(any(x["text"].startswith("[acp] fallback to cli: initialize") for x in self.rows()), self.rows())

    def test_auth_required_no_fallback(self):
        os.environ["FAKE_MODE"] = "auth"; self.add()
        r = ag.run_turn(self.sd, "g", "x")
        self.assertEqual(r["exit"], 1, r); self.assertIn("ag agents doctor", r["reply"])
        self.assertFalse(any("cli" in c for c in self.calls()))

    def test_model_set_or_fallback(self):
        self.add("o", "opencode", "--model", "m/b")
        r = ag.run_turn(self.sd, "o", "x"); self.assertEqual(r["exit"], 0, r)
        sc = [c for c in self.calls() if c.get("method") == "session/set_config_option"]
        self.assertEqual(sc[0]["params"], {"sessionId": "s-new", "configId": "model", "value": "m/b"})
        self.assertEqual(self.calls()[0]["acp"], ["acp", "--cwd", str(self.w)])
        self.log.write_text("")
        self.add("o2", "opencode", "--model", "m/zzz")
        r = ag.run_turn(self.sd, "o2", "x"); self.assertEqual(r["exit"], 0, r); self.assertEqual(r["reply"], "cli-ok")
        self.assertNotIn("session/prompt", self.methods())
        cl = [c["cli"] for c in self.calls() if "cli" in c][0]
        self.assertEqual(cl[:3], ["run", "--format", "json"]); self.assertIn("m/zzz", cl)

    def test_generic_acp_backend_and_killswitch(self):
        o = lambda p: p.stdout + p.stderr
        self.assertIn("--acp-cmd", o(self.cli("agents", "add", "x", "--backend", "acp", ok=False)))
        self.assertIn("transport acp needs", o(self.cli("agents", "add", "y", "--backend", "codex", "--transport", "acp", ok=False)))
        self.cli("agents", "add", "x", "--backend", "acp", "--acp-cmd", f"{self.bin}/gemini --acp", "--dir", str(self.w))
        r = ag.run_turn(self.sd, "x", "hi"); self.assertEqual(r["exit"], 0, r); self.assertEqual(r["reply"], "Hello\n after")
        self.add()
        os.environ["AG_ACP"] = "0"; self.log.write_text("")
        r = ag.run_turn(self.sd, "g", "hi"); self.assertEqual(r["reply"], "cli-ok")
        self.assertIn("AG_ACP=0", ag.run_turn(self.sd, "x", "hi").get("error", ""))
        del os.environ["AG_ACP"]
        self.cli("agents", "set", "g", "--transport", "cli"); self.log.write_text("")
        self.assertEqual(ag.run_turn(self.sd, "g", "hi")["reply"], "cli-ok")

if __name__ == "__main__":
    unittest.main()
