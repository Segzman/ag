#!/usr/bin/env python3
"""ag merge + ag agents gc: real git repos in temp dirs, echo agents only, no network."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True,
                       env=dict(os.environ, **GIT_ID))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class Merge(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-merge-t-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.repo, self.state = self.td / "repo", self.td / "state"
        self.repo.mkdir(); git(self.repo, "init", "-q", "-b", "master")
        (self.repo / "base.txt").write_text("base\n"); git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "base")
        self.env = dict(os.environ, **GIT_ID, AG_HOME=str(self.td / "home"), AG_CONFIG_HOME=str(self.td / "cfg"),
                        AG_CACHE_HOME=str(self.td / "cache"), AG_AUTO_UPDATE="0")
        self.wt = self.repo.parent / "repo.ag-wt" / "w1"
        self.run_ag("agents", "add", "w1", "--backend", "echo", "--worktree", "--dir", str(self.repo))

    def run_ag(self, *a, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), *a], capture_output=True, text=True,
                           env=self.env, timeout=60)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def row(self, name="w1"):
        return next(r for r in json.loads((self.state / "agents.json").read_text()) if r["name"] == name)

    def commit_in_wt(self, fname, text="x\n"):
        (self.wt / fname).write_text(text); git(self.wt, "add", "-A"); git(self.wt, "commit", "-qm", "work " + fname)

    def test_merge_removes_worktree_branch_and_marks_dormant(self):
        self.commit_in_wt("new.txt")
        p = self.run_ag("merge", "w1", "--verify", "test -f new.txt")
        self.assertIn("new.txt", p.stdout)  # diff stat shown
        self.assertTrue((self.repo / "new.txt").exists())
        self.assertFalse(self.wt.exists())
        self.assertEqual(git(self.repo, "branch", "--list", "ag/w1"), "")
        r = self.row()
        self.assertTrue(r["dormant"]); self.assertNotIn("worktree", r); self.assertEqual(Path(r["dir"]).resolve(), self.repo.resolve())

    def test_keep_leaves_worktree_and_branch(self):
        self.commit_in_wt("k.txt")
        self.run_ag("merge", "w1", "--keep")
        self.assertTrue((self.repo / "k.txt").exists())
        self.assertTrue(self.wt.is_dir()); self.assertIn("ag/w1", git(self.repo, "branch", "--list"))
        self.assertNotIn("dormant", self.row())

    def test_squash_makes_one_commit(self):
        self.commit_in_wt("s1.txt"); self.commit_in_wt("s2.txt")
        before = int(git(self.repo, "rev-list", "--count", "HEAD"))
        self.run_ag("merge", "w1", "--squash")
        self.assertEqual(int(git(self.repo, "rev-list", "--count", "HEAD")), before + 1)
        self.assertTrue((self.repo / "s2.txt").exists())

    def test_refuses_dirty_worktree(self):
        self.commit_in_wt("d.txt"); (self.wt / "dirty.txt").write_text("u\n")
        p = self.run_ag("merge", "w1", ok=False)
        self.assertNotEqual(p.returncode, 0); self.assertIn("uncommitted changes", p.stderr)
        self.assertFalse((self.repo / "d.txt").exists())

    def test_kit_codegraph_index_does_not_block_merge(self):
        self.commit_in_wt("cg.txt")
        (self.wt / ".codegraph").mkdir(); (self.wt / ".codegraph" / ".gitignore").write_text("*\n")
        self.run_ag("merge", "w1")
        self.assertTrue((self.repo / "cg.txt").exists()); self.assertFalse(self.wt.exists())

    def test_refuses_dirty_main_checkout(self):
        self.commit_in_wt("m.txt"); (self.repo / "base.txt").write_text("changed\n")
        p = self.run_ag("merge", "w1", ok=False)
        self.assertIn("main checkout", p.stderr); self.assertIn("uncommitted", p.stderr)
        self.assertTrue(self.wt.is_dir())

    def test_verify_failure_merges_nothing(self):
        self.commit_in_wt("v.txt")
        p = self.run_ag("merge", "w1", "--verify", "false", ok=False)
        self.assertIn("verify failed", p.stderr)
        self.assertFalse((self.repo / "v.txt").exists()); self.assertTrue(self.wt.is_dir())

    def test_conflict_aborts_and_reports_files(self):
        self.commit_in_wt("base.txt", "agent side\n")
        (self.repo / "base.txt").write_text("main side\n"); git(self.repo, "commit", "-qam", "main edit")
        before = git(self.repo, "rev-parse", "HEAD")
        p = self.run_ag("merge", "w1", ok=False)
        self.assertIn("base.txt", p.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertTrue(self.wt.is_dir())

    def test_into_other_branch_refused_when_main_on_master(self):
        self.commit_in_wt("o.txt")
        p = self.run_ag("merge", "w1", "--into", "dev", ok=False)
        self.assertIn("not dev", p.stderr)


class Gc(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-gc-t-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.repo, self.state = self.td / "repo", self.td / "state"
        self.repo.mkdir(); git(self.repo, "init", "-q", "-b", "master")
        (self.repo / "base.txt").write_text("base\n"); git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "base")
        self.env = dict(os.environ, **GIT_ID, AG_HOME=str(self.td / "home"), AG_CONFIG_HOME=str(self.td / "cfg"),
                        AG_CACHE_HOME=str(self.td / "cache"), AG_AUTO_UPDATE="0")

    def run_ag(self, *a, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), *a], capture_output=True, text=True,
                           env=self.env, timeout=60)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def names(self):
        return {r["name"] for r in json.loads((self.state / "agents.json").read_text())}

    def old_chat(self, name, days):
        d = self.state / "chats"; d.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - days * 86400))
        (d / f"{name}.jsonl").write_text(json.dumps({"ts": ts, "role": "user", "text": "hi"}) + "\n")

    def failed_wake(self, name):
        d = self.state / "wake" / "jobs"; d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}-job.json").write_text(json.dumps({"id": f"{name}-job", "agent": name, "status": "failed",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}))

    def test_default_is_dry_run_and_yes_removes_idle(self):
        self.run_ag("agents", "add", "old", "--backend", "echo", "--dir", str(self.repo))
        self.run_ag("agents", "add", "fresh", "--backend", "echo", "--dir", str(self.repo))
        self.old_chat("old", 30); self.old_chat("fresh", 1)
        p = self.run_ag("agents", "gc")
        self.assertIn("remove old", p.stdout); self.assertIn("old", self.names())
        self.run_ag("agents", "gc", "--yes")
        self.assertNotIn("old", self.names()); self.assertIn("fresh", self.names())

    def test_failed_flag_removes_last_wake_failed_only_with_flag(self):
        self.run_ag("agents", "add", "bad", "--backend", "echo", "--dir", str(self.repo))
        self.run_ag("agents", "add", "ok", "--backend", "echo", "--dir", str(self.repo))
        self.failed_wake("bad")
        self.run_ag("agents", "gc", "--yes")
        self.assertIn("bad", self.names())
        self.run_ag("agents", "gc", "--failed", "--yes")
        self.assertNotIn("bad", self.names()); self.assertIn("ok", self.names())

    def test_unmerged_worktree_is_kept_and_listed(self):
        self.run_ag("agents", "add", "keep", "--backend", "echo", "--worktree", "--dir", str(self.repo))
        wt = self.repo.parent / "repo.ag-wt" / "keep"
        (wt / "x.txt").write_text("x\n"); git(wt, "add", "-A"); git(wt, "commit", "-qm", "x")
        self.old_chat("keep", 30)
        p = self.run_ag("agents", "gc", "--yes")
        self.assertIn("keep", self.names()); self.assertTrue(wt.is_dir())
        self.assertIn("unmerged", p.stdout)

    def test_merged_worktree_removed_with_agent(self):
        self.run_ag("agents", "add", "gone", "--backend", "echo", "--worktree", "--dir", str(self.repo))
        wt = self.repo.parent / "repo.ag-wt" / "gone"
        self.old_chat("gone", 30)
        self.run_ag("agents", "gc", "--yes")
        self.assertNotIn("gone", self.names()); self.assertFalse(wt.exists())
        self.assertEqual(git(self.repo, "branch", "--list", "ag/gone"), "")

    def test_agents_without_recorded_activity_are_kept(self):
        self.run_ag("agents", "add", "new", "--backend", "echo", "--dir", str(self.repo))
        self.run_ag("agents", "gc", "--idle-days", "0", "--yes")
        self.assertIn("new", self.names())


if __name__ == "__main__":
    unittest.main()
