#!/usr/bin/env python3
"""ag serve --acp: ag as an ACP agent over stdio. Stdlib, temp dirs, no network, echo backend only.

Spawns `ag serve --acp` as a subprocess against a temp state dir (.agent) with an
echo-backend agent, drives initialize / session/new / session/prompt as an ACP
client (JSON-RPC 2.0, newline-delimited), and asserts the streamed
agent_message_chunk notifications and the final stopReason.
Run: python3 -m pytest -q tests/test_ag_acp_server.py
"""
import json, os, subprocess, sys, tempfile, threading
from pathlib import Path

AG = Path(__file__).resolve().parent.parent / "ag"
assert AG.exists(), f"missing ag binary: {AG}"


def run_ag(state, *args, timeout=30, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
                          capture_output=True, text=True, timeout=timeout, env=e)


class Client:
    """Minimal ACP client: one JSON object per line, responses matched by id,
    notifications collected while waiting."""

    def __init__(self, state, *serve_args):
        self.p = subprocess.Popen([sys.executable, str(AG), "--dir", str(state), "serve", "--acp", *serve_args],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, AG_SECRET_POPUP="0", AG_AUTO_UPDATE="0"),  # no secret hint in the echoed prompt
                                  cwd=str(state))  # clean cwd: no project context files swell the echo prompt
        self.notes = []          # every notification seen, in order
        self.perm_requests = []  # session/request_permission requests from the server
        self._buf = []
        self._n = 0
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True); self._reader.start()

    def _read(self):
        for line in self.p.stdout:
            try: m = json.loads(line)
            except Exception: continue
            with self._lock: self._buf.append(m)

    def _take(self, pred):
        with self._lock:
            for i, m in enumerate(self._buf):
                if pred(m): return self._buf.pop(i)
        return None

    def send_raw(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n"); self.p.stdin.flush()

    def call(self, method, params=None, timeout=60):
        self._n += 1; rid = self._n
        self.send_raw({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.wait_response(rid, timeout)

    def wait_response(self, rid, timeout=60):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = self._take(lambda x: x.get("id") == rid and "method" not in x)
            if m: return m
            m = self._take(lambda x: "method" in x)
            if m:
                if m.get("method") == "session/request_permission": self.perm_requests.append(m)
                else: self.notes.append(m)
            time.sleep(0.02)
        raise AssertionError(f"no response to id={rid}; notes={self.notes} stderr={self.stderr()}")

    def chunks(self):
        return [n["params"]["update"]["content"]["text"] for n in self.notes
                if n.get("method") == "session/update"
                and (n["params"].get("update") or {}).get("sessionUpdate") == "agent_message_chunk"]

    def stderr(self):
        if self.p.poll() is None: return "(server still running)"
        try: return self.p.stderr.read()
        except Exception: return ""

    def close(self):
        try: self.p.stdin.close()
        except Exception: pass
        try: self.p.wait(timeout=15)
        except Exception: self.p.kill()


def _state_with_echo():
    td = Path(tempfile.mkdtemp(prefix="agacp-"))
    p = run_ag(td, "agents", "add", "echoer", "--backend", "echo", "--role", "sub")
    assert p.returncode == 0, p.stderr
    return td


def test_acp_round_trip_streams_chunks_and_stop_reason():
    td = _state_with_echo()
    c = Client(td, "--agent", "echoer")
    try:
        r = c.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        assert r["result"]["protocolVersion"] == 1, r
        assert r["result"]["agentInfo"]["name"] == "ag", r
        assert r["result"]["agentCapabilities"]["loadSession"] is False, r
        r = c.call("session/new", {"cwd": str(td), "mcpServers": []})
        sid = r["result"]["sessionId"]; assert sid.startswith("acp-"), r
        assert r["result"]["modes"]["currentModeId"] == "edits", r
        r = c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "hello acp world"}]})
        assert r["result"]["stopReason"] == "end_turn", r
        chunks = c.chunks()
        assert chunks, f"no streamed chunks; notes={c.notes}"
        assert "hello acp world" in "".join(chunks), chunks
        assert all(n["params"]["sessionId"] == sid for n in c.notes if n.get("method") == "session/update")
        # second prompt on the same session still works
        r = c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "second turn"}]})
        assert r["result"]["stopReason"] == "end_turn", r
        assert len(c.chunks()) > len(chunks)  # echo clips a history-replayed prompt, so count chunks instead of matching text
    finally:
        c.close()
    assert c.p.returncode == 0, c.stderr()


def test_acp_errors_are_clear():
    td = _state_with_echo()
    c = Client(td, "--agent", "echoer")
    try:
        c.call("initialize", {"protocolVersion": 1})
        r = c.call("session/prompt", {"sessionId": "acp-nope", "prompt": [{"type": "text", "text": "hi"}]})
        assert r["error"]["code"] == -32602 and "unknown session" in r["error"]["message"], r
        r = c.call("bogus/method", {})
        assert r["error"]["code"] == -32601 and "not supported" in r["error"]["message"], r
        r = c.call("session/load", {"sessionId": "x", "cwd": str(td), "mcpServers": []})
        assert r["error"]["code"] == -32601 and "session/new" in r["error"]["message"], r
        r = c.call("session/new", {"cwd": str(td), "mcpServers": []})
        sid = r["result"]["sessionId"]
        r = c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "image", "data": "x"}]})
        assert r["error"]["code"] == -32602 and "empty prompt" in r["error"]["message"], r
        # cancel is a notification: no response, server stays alive
        c.send_raw({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}})
        r = c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "after cancel"}]})
        assert r["result"]["stopReason"] == "end_turn", r
    finally:
        c.close()


def test_acp_unknown_agent_and_job_flag():
    td = _state_with_echo()
    c = Client(td, "--agent", "ghost")
    try:
        c.call("initialize", {"protocolVersion": 1})
        r = c.call("session/new", {"cwd": str(td), "mcpServers": []})
        assert r["error"]["code"] == -32602 and "no such agent: ghost" in r["error"]["message"], r
    finally:
        c.close()
    p = run_ag(td, "serve", "--acp", "--agent", "echoer", "--job", "implement", env={"AG_AUTO_UPDATE": "0"})
    # --agent + --job together are refused before the server starts (stdin EOF -> exit via emit error path)
    assert p.returncode != 0 or "alternatives" in (p.stdout + p.stderr), (p.returncode, p.stdout, p.stderr)


def test_acp_default_agent_is_first_roster_row():
    td = Path(tempfile.mkdtemp(prefix="agacp-"))
    (td / "agents.json").write_text(json.dumps(
        [{"name": "solo", "backend": "echo", "model": "default", "dir": ".", "role": "sub"}]))
    c = Client(td)
    try:
        c.call("initialize", {"protocolVersion": 1})
        r = c.call("session/new", {"cwd": str(td), "mcpServers": []})
        sid = r["result"]["sessionId"]
        r = c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "default agent hi"}]})
        assert r["result"]["stopReason"] == "end_turn", r
        assert "default agent hi" in "".join(c.chunks())
    finally:
        c.close()


if __name__ == "__main__":
    for n, f in sorted({k: v for k, v in list(globals().items()) if k.startswith("test_")}.items()):
        f(); print("ok", n)
