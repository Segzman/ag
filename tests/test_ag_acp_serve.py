#!/usr/bin/env python3
"""`ag acp serve`: ag as an ACP agent (T3 Code client side). Drives the server over pipes with the keyless
`echo` backend, plus a tiny fake ACP agent (backend acp) for tool streaming and cancel.
Run: python3 tests/test_ag_acp_serve.py"""
import json, os, queue, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''import json, os, sys, time
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def upd(sid, u): out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
for line in sys.stdin:
    m = json.loads(line); meth, i, p = m.get("method"), m.get("id"), m.get("params") or {}
    if meth == "initialize": out({"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif meth == "session/new": out({"jsonrpc": "2.0", "id": i, "result": {"sessionId": "inner"}})
    elif meth == "session/prompt":
        if os.environ.get("FAKE_MODE") == "hang":
            for l in sys.stdin: pass
            sys.exit(0)
        upd("inner", {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "ls -la", "kind": "execute", "status": "pending"})
        upd("inner", {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "file-a"}}]})
        upd("inner", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done-ok"}})
        out({"jsonrpc": "2.0", "id": i, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 7, "outputTokens": 2}}})
'''


class Srv:
    def __init__(self, t, *extra):
        self.p = subprocess.Popen([sys.executable, AG, "--dir", str(t.sd), "acp", "serve", *extra], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=t.env, cwd=t.td)
        self.q, self.n, self.got = queue.Queue(), 0, []
        threading.Thread(target=self._rd, daemon=True).start()
        threading.Thread(target=lambda: self.p.stderr.read(), daemon=True).start()
    def _rd(self):
        for l in self.p.stdout: self.q.put(json.loads(l))  # any non-JSON stdout fails the test here
        self.q.put(None)
    def send(self, o): self.p.stdin.write((json.dumps(o) + "\n").encode()); self.p.stdin.flush()
    def req(self, method, params=None, wait=True):
        self.n += 1; self.send({"jsonrpc": "2.0", "id": self.n, "method": method, **({"params": params} if params is not None else {})})
        return self.resp(self.n) if wait else self.n
    def resp(self, i, timeout=60):
        end = time.monotonic() + timeout
        while True:
            m = self.q.get(timeout=max(0.01, end - time.monotonic()))
            assert m is not None, "server exited"
            if m.get("id") == i and "method" not in m: return m
            self.got.append(m)
    def updates(self, kind=None):
        u = [m["params"]["update"] for m in self.got if m.get("method") == "session/update"]
        return [x for x in u if kind is None or x.get("sessionUpdate") == kind]
    def close(self):
        try: self.p.stdin.close()
        except Exception: pass
        return self.p.wait(timeout=30)


class AcpServe(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); td = self.td
        self.sd, self.w = Path(td, ".agent"), Path(td, "w"); self.w.mkdir()
        Path(td, "fake.py").write_text(FAKE)
        self.env = dict(os.environ, AG_AUTO_UPDATE="0", AG_HOME=td + "/h", AG_CONFIG_HOME=td + "/c", AG_CACHE_HOME=td + "/k",
            AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0", AG_CHECKPOINT="0", FAKE_MODE="normal")
        self.cli("agents", "add", "e1", "--backend", "echo", "--dir", str(self.w))
    def cli(self, *a):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p
    def roster(self): return {x["name"]: x for x in json.loads((self.sd / "agents.json").read_text())}

    def test_session_flow(self):
        s = Srv(self)
        r = s.req("initialize", {"protocolVersion": 3, "clientCapabilities": {}})["result"]
        self.assertEqual(r["protocolVersion"], 1); self.assertTrue(r["agentCapabilities"]["loadSession"])
        self.assertEqual(r["agentInfo"]["name"], "ag"); self.assertEqual(r["authMethods"], [])
        bad = s.req("session/new", {"cwd": self.td + "/elsewhere", "mcpServers": []})  # "." roster dirs = server cwd (td)
        self.assertEqual(bad["error"]["code"], -32602); self.assertIn("agents add", bad["error"]["message"])
        r = s.req("session/new", {"cwd": str(self.w), "mcpServers": []})["result"]
        sid = r["sessionId"]; self.assertTrue(sid.startswith("e1:"))
        self.assertEqual(r["modes"]["currentModeId"], "default")
        self.assertEqual([m["id"] for m in r["modes"]["availableModes"]], ["default", "ro", "edits", "auto", "full"])
        self.assertEqual({c["id"] for c in r["configOptions"]}, {"agent", "model"})
        r = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "hello acp"},
            {"type": "resource", "resource": {"uri": "file:///x.txt", "text": "ctx-body"}}]})["result"]
        self.assertEqual(r["stopReason"], "end_turn")
        cmds = s.updates("available_commands_update")[0]["availableCommands"]
        self.assertIn("model", [c["name"] for c in cmds]); self.assertNotIn("handoff", [c["name"] for c in cmds])
        txt = "".join(u["content"]["text"] for u in s.updates("agent_message_chunk"))
        self.assertIn("hello acp", txt); self.assertIn("ctx-body", txt)
        r = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "/harness current"}]})["result"]
        self.assertEqual(r["stopReason"], "end_turn")
        self.assertIn("backend echo", s.updates("agent_message_chunk")[-1]["content"]["text"])
        self.assertEqual(s.req("session/set_mode", {"sessionId": sid, "modeId": "ro"})["result"], {})
        self.assertEqual(self.roster()["e1"]["mode"], "ro")
        self.assertEqual(s.updates("current_mode_update")[-1]["currentModeId"], "ro")
        self.assertEqual(s.req("session/set_mode", {"sessionId": sid, "modeId": "nope"})["error"]["code"], -32602)
        self.assertEqual(s.req("session/set_mode", {"sessionId": sid, "modeId": "default"})["result"], {})
        self.assertFalse(self.roster()["e1"].get("mode"))
        r = s.req("session/set_config_option", {"sessionId": sid, "configId": "model", "value": "custom-x"})["result"]
        self.assertEqual(next(c for c in r["configOptions"] if c["id"] == "model")["currentValue"], "custom-x")
        self.assertEqual(s.req("foo/bar", {})["error"]["code"], -32601)
        self.assertEqual(s.req("session/prompt", {"sessionId": "ghost:1", "prompt": [{"type": "text", "text": "x"}]})["error"]["code"], -32602)
        s.send({"jsonrpc": "2.0", "method": "foo/notify", "params": {}})  # unknown notification: ignored, no reply
        self.assertIn("result", s.req("initialize", {"protocolVersion": 1}))
        self.assertEqual(s.close(), 0)
        # restart: session/load replays the chat log for the same sessionId
        s = Srv(self); s.req("initialize", {"protocolVersion": 1})
        r = s.req("session/load", {"sessionId": sid, "cwd": str(self.w), "mcpServers": []})
        self.assertIn("modes", r["result"])
        self.assertIn("hello acp", "".join(u["content"]["text"] for u in s.updates("user_message_chunk")))
        self.assertIn("hello acp", "".join(u["content"]["text"] for u in s.updates("agent_message_chunk")))
        s.req("initialize", {"protocolVersion": 1})  # commands update follows the load response
        self.assertTrue(s.updates("available_commands_update"))
        self.assertEqual(s.close(), 0)

    def test_tools_usage_and_pinned_agent(self):
        self.cli("agents", "add", "fk", "--backend", "acp", "--acp-cmd", f"{sys.executable} {self.td}/fake.py", "--dir", self.td)
        s = Srv(self, "--agent", "fk"); s.req("initialize", {"protocolVersion": 1})
        r = s.req("session/new", {"cwd": "/", "mcpServers": []})["result"]
        sid = r["sessionId"]; self.assertEqual([c["id"] for c in r["configOptions"]], ["model"])
        r = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]})["result"]
        self.assertEqual(r["stopReason"], "end_turn"); self.assertEqual(r["usage"]["outputTokens"], 2)
        tc = s.updates("tool_call"); self.assertEqual(len(tc), 1, tc)
        self.assertEqual((tc[0]["kind"], tc[0]["status"]), ("execute", "in_progress"))
        done = [u for u in s.updates("tool_call_update") if u["toolCallId"] == tc[0]["toolCallId"] and u.get("content")]
        self.assertEqual(done[0]["status"], "completed"); self.assertEqual(done[0]["content"][0]["content"]["text"], "file-a")
        self.assertEqual("".join(u["content"]["text"] for u in s.updates("agent_message_chunk")), "done-ok")
        self.assertEqual(s.close(), 0)

    def test_cancel(self):
        self.env["FAKE_MODE"] = "hang"
        self.cli("agents", "add", "fk", "--backend", "acp", "--acp-cmd", f"{sys.executable} {self.td}/fake.py", "--dir", self.td)
        s = Srv(self); s.req("initialize", {"protocolVersion": 1})
        sid = s.req("session/new", {"cwd": self.td, "mcpServers": []})["result"]["sessionId"]
        i = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "slow"}]}, wait=False)
        time.sleep(2)
        busy = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "again"}]})
        self.assertIn("already running", busy["error"]["message"])  # requests still served mid-turn
        s.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}})
        self.assertEqual(s.resp(i, 40)["result"]["stopReason"], "cancelled")
        q = json.loads(subprocess.run([sys.executable, AG, "--dir", str(self.sd), "--json", "queue", "fk"], capture_output=True, text=True, env=self.env).stdout)
        self.assertFalse(q["data"].get("held"), q)  # non-holding stop
        self.assertEqual(s.close(), 0)

    def test_eof_stops_running_turn(self):
        self.env["FAKE_MODE"] = "hang"
        self.cli("agents", "add", "fk", "--backend", "acp", "--acp-cmd", f"{sys.executable} {self.td}/fake.py", "--dir", self.td)
        s = Srv(self); s.req("initialize", {"protocolVersion": 1})
        sid = s.req("session/new", {"cwd": self.td, "mcpServers": []})["result"]["sessionId"]
        i = s.req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "slow"}]}, wait=False)
        time.sleep(2); t0 = time.monotonic()
        self.assertEqual(s.close(), 0); self.assertLess(time.monotonic() - t0, 25)
        self.assertEqual(s.resp(i, 5)["result"]["stopReason"], "cancelled")


if __name__ == "__main__":
    unittest.main()
