"""Isolate ag state from the real machine for every pytest test.

Without this, tests read the developer's live ~/.cache/ag (usage-limit
cooldowns fail unrelated tests), ~/.config/ag and ~/AGENTS.md (global style
text leaks into exact-prompt assertions). Tests that set these vars themselves
(subprocess env dicts, or os.environ inside the test body) still override this.
"""
import pytest


@pytest.fixture(autouse=True)
def _ag_isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AG_HOME", str(tmp_path / "ag-home"))
    monkeypatch.setenv("AG_CONFIG_HOME", str(tmp_path / "ag-config"))
    monkeypatch.setenv("AG_CACHE_HOME", str(tmp_path / "ag-cache"))
    # cold cache would make ag probe backend binaries (fake ones included) for auth/version
    monkeypatch.setenv("AG_PROBE", "0")
    # kit default "all" would inject the style block into every composed prompt
    monkeypatch.setenv("AG_KIT", "none")
    # real opencode 2.x on the box: ag would exec `opencode --version` (extra fake-binary call) and switch argv shape
    monkeypatch.setenv("AG_OPENCODE_MAJOR", "1")
    # No test may reach osascript / Notification Center or a real ag-auth popup: ui_run() execs AG_UI_CMD instead
    # of osascript, and AG_AUTH_HELPER (honoured outside agent envs) is a deny-stub. Tests that need a log set their own.
    noop = tmp_path / "noop-ui"; noop.write_text("#!/bin/sh\nexit 0\n"); noop.chmod(0o755)
    deny = tmp_path / "deny-auth"; deny.write_text("#!/bin/sh\nexit 1\n"); deny.chmod(0o755)
    monkeypatch.setenv("AG_UI_CMD", str(noop))
    monkeypatch.setenv("AG_AUTH_HELPER", str(deny))
    # human gate: tests drive gated commands directly; the gate itself is tested in test_ag_human_gate.py.
    # Agent-runtime markers are scrubbed so a run inside Claude Code behaves like a plain terminal.
    monkeypatch.setenv("AG_HUMAN_GATE", "off")
    for k in ("AG_JOB_ID", "AG_PARENT_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENCODE", "OPENCODE_PID", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_CI"):
        monkeypatch.delenv(k, raising=False)
