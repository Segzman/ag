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
