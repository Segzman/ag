#!/usr/bin/env python3
"""Worktree-escape fixes (opencode 2.x agents editing the MAIN checkout instead of their worktree).

Root causes covered offline with a fake `opencode`:
  1. a wake worker inherits the launcher's $PWD (main checkout); `opencode run` trusts $PWD over getcwd(),
     so the session (and its tools) land in main -> run_turn must export PWD=<workdir>.
  2. a session keeps its creation directory on resume -> oc_resume_guard moves it (or drops the sid).
  3. post-turn guard: any change in the main checkout during a worktree agent's turn fails the turn.
  4. ro/edits/profile turns run `--standalone` (the shared service ignores per-turn OPENCODE_CONFIG).
  5. AG_SANDBOX=1 wraps opencode in a macOS sandbox-exec profile denying writes to main (not .git).

Run: python3 -m unittest tests.test_ag_worktree_escape
"""
import importlib.machinery, importlib.util, json, os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
os.environ.setdefault("AG_AUTO_UPDATE", "0")

# Fake opencode 2.x: reports $PWD / getcwd, optionally writes into $ESCAPE_FILE (the "escape"), serves `api` from files.
FAKE = r'''#!/bin/sh
case "$1" in
  --version) echo "2.3.0" ;;
  api)
    case "$2" in
      GET) cat "$FAKE_STATE/session.json" 2>/dev/null || echo '{}' ;;
      POST) [ "$FAKE_MOVE_FAILS" = 1 ] && exit 1; cp "$FAKE_STATE/moved.json" "$FAKE_STATE/session.json"; echo '{}' ;;
    esac ;;
  run)
    echo "$@" > "$FAKE_STATE/argv"
    printf '{"type":"text","sessionID":"ses_fake","part":{"type":"text","text":"PWD=%s CWD=%s"}}\n' "$PWD" "$(pwd -P)"
    [ -n "$ESCAPE_FILE" ] && echo oops >> "$ESCAPE_FILE"
    printf '{"type":"step_finish","part":{"reason":"stop"}}\n' ;;
esac
'''


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd)] + list(a), check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class Fix(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agwe-")).resolve()
        self.main, self.wt, self.sd = self.td / "main", self.td / "main.ag-wt" / "w", self.td / "st"
        self.main.mkdir(); git(self.main, "init", "-q", "-b", "master")
        (self.main / "ag").write_text("original\n"); (self.main / "other.txt").write_text("x\n")
        git(self.main, "add", "."); git(self.main, "commit", "-q", "-m", "init")
        git(self.main, "worktree", "add", "-q", "-b", "ag/w", str(self.wt), "master")
        self.bin, self.state = self.td / "bin", self.td / "fake"
        self.bin.mkdir(); self.state.mkdir()
        (self.bin / "opencode").write_text(FAKE); (self.bin / "opencode").chmod(0o755)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_STATE": str(self.state), "AG_OPENCODE_MAJOR": "2", "AG_MODEL_CHECK": "0", "PWD": str(self.main)}
        self._old = {k: os.environ.get(k) for k in list(self.env) + ["ESCAPE_FILE", "FAKE_MOVE_FAILS", "AG_ESCAPE_GUARD", "AG_SANDBOX"]}
        os.environ.update(self.env)
        l = importlib.machinery.SourceFileLoader("ag_we_t", AG)
        self.m = importlib.util.module_from_spec(importlib.util.spec_from_loader("ag_we_t", l)); l.exec_module(self.m)
        self.m.ensure_state(self.sd)
        self.m.save_agents(self.sd, [{"name": "w", "backend": "opencode", "model": "opencode-go/x", "dir": str(self.wt), "role": "sub", "mode": "auto"}])
        os.chdir(self.main)

    def tearDown(self):
        os.chdir("/")
        for k, v in self._old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        shutil.rmtree(self.td, ignore_errors=True)

    def turn(self, **kw):
        return self.m.run_turn(self.sd, "w", "hi", **kw)

    # 1. PWD is the Popen cwd, not the launcher's
    def test_turn_exports_pwd_of_worktree(self):
        r = self.turn()
        self.assertNotIn("error", r)
        self.assertIn(f"PWD={self.wt} ", r["reply"]); self.assertIn(f"CWD={self.wt}", r["reply"])

    def test_pwd_env_helper(self):
        self.assertEqual(self.m._pwd_env({"PWD": "/a", "K": "v"}, "/b"), {"PWD": "/b", "K": "v"})
        self.assertEqual(self.m._pwd_env(None, "/b")["PWD"], "/b")

    # 2. session bound to main is moved to the worktree (or dropped)
    def _session(self, directory):
        (self.state / "session.json").write_text(json.dumps({"data": {"id": "ses_old", "location": {"directory": directory}}}))
        (self.state / "moved.json").write_text(json.dumps({"data": {"id": "ses_old", "location": {"directory": str(self.wt)}}}))

    def test_resume_guard_moves_poisoned_session(self):
        self._session(str(self.main))
        sid, note = self.m.oc_resume_guard("ses_old", str(self.wt))
        self.assertEqual(sid, "ses_old"); self.assertIn("moved to", note)

    def test_resume_guard_drops_sid_when_move_fails(self):
        self._session(str(self.main)); os.environ["FAKE_MOVE_FAILS"] = "1"
        sid, note = self.m.oc_resume_guard("ses_old", str(self.wt))
        self.assertEqual(sid, ""); self.assertIn("fresh session", note)

    def test_resume_guard_noop_when_dir_matches_or_unknown(self):
        self._session(str(self.wt))
        self.assertEqual(self.m.oc_resume_guard("ses_old", str(self.wt)), ("ses_old", ""))
        (self.state / "session.json").unlink()
        self.assertEqual(self.m.oc_resume_guard("ses_old", str(self.wt)), ("ses_old", ""))

    def test_turn_resumes_after_move(self):
        self._session(str(self.main))
        meta = self.m.load_chat_meta(self.sd, "w"); meta["sid"] = "ses_old"; meta["sids"] = {"opencode": "ses_old"}
        self.m.save_chat_meta(self.sd, "w", meta)
        r = self.turn()
        self.assertNotIn("error", r)
        self.assertIn("--session ses_old", (self.state / "argv").read_text())
        self.assertIn("moved to", "\n".join(x["text"] for x in self.m.load_chat_log(self.sd, "w")))

    # 3. post-turn guard
    def test_guard_fails_turn_that_touches_main(self):
        os.environ["ESCAPE_FILE"] = str(self.main / "ag")
        r = self.turn()
        self.assertEqual(r["exit"], 1)
        self.assertIn("worktree escape", r["stderr"])
        self.assertIn(str(self.main), r["stderr"]); self.assertIn(": ag.", r["stderr"])

    def test_guard_catches_untracked_new_file_and_chmod(self):
        os.environ["ESCAPE_FILE"] = str(self.main / "new.txt")
        self.assertIn("worktree escape", self.turn().get("stderr", ""))
        snap = self.m.escape_snapshot(str(self.wt))
        os.chmod(self.main / "other.txt", 0o444)
        self.assertEqual(self.m.escape_changes(snap), ["other.txt"])

    def test_guard_quiet_when_agent_stays_in_worktree(self):
        os.environ["ESCAPE_FILE"] = str(self.wt / "ag")
        self.assertNotIn("error", self.turn())

    def test_guard_off_switch_and_non_worktree(self):
        os.environ["ESCAPE_FILE"] = str(self.main / "ag"); os.environ["AG_ESCAPE_GUARD"] = "0"
        self.assertNotIn("error", self.turn())
        self.assertIsNone(self.m.escape_snapshot(str(self.main)))  # main checkout itself: nothing to guard
        self.assertEqual(self.m.main_checkout_of(str(self.wt)), str(self.main))
        self.assertEqual(self.m.main_checkout_of(str(self.td)), "")

    # 4. enforcement needs a private server
    def _argv(self, **agent):
        a = {"name": "w", "backend": "opencode", "model": "m", "dir": str(self.wt), **agent}
        return self.m._turn_argv(self.sd, a, "opencode", "m", str(self.wt), "", "p", "", None, "")

    def test_standalone_for_restricted_modes_only(self):
        self.assertIn("--standalone", self._argv(mode="edits"))
        self.assertIn("--standalone", self._argv(mode="ro"))
        self.assertIn("--standalone", self._argv(plan=True))
        self.assertNotIn("--standalone", self._argv(mode="auto"))
        self.assertEqual(self._argv(mode="edits")[1], "run")  # still `opencode run ...`

    # 5. optional sandbox
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("sandbox-exec"), "macOS sandbox-exec only")
    def test_sandbox_denies_main_writes_not_worktree_or_git(self):
        os.environ["AG_SANDBOX"] = "1"
        argv = self._argv(mode="auto")
        self.assertEqual(os.path.basename(argv[0]), "sandbox-exec"); self.assertIn("--standalone", argv)
        prof = argv[2]
        run = lambda cmd: subprocess.run(["sandbox-exec", "-p", prof, "sh", "-c", cmd], cwd=self.wt, capture_output=True, text=True).returncode
        self.assertNotEqual(run(f"echo x >> {self.main}/ag"), 0)
        self.assertNotEqual(run(f"echo x > {self.main}/new.txt"), 0)
        self.assertEqual(run(f"echo x > {self.wt}/new.txt"), 0)
        self.assertEqual(run(f"git -C {self.wt} add new.txt && git -C {self.wt} -c user.name=t -c user.email=t@t commit -qm c"), 0)
        os.environ.pop("AG_SANDBOX")
        self.assertNotEqual(os.path.basename(self._argv(mode="auto")[0]), "sandbox-exec")


if __name__ == "__main__":
    unittest.main()
