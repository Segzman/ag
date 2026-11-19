#!/usr/bin/env python3
"""--require-diff: verify must see work — a wake whose turn changes nothing in
the agent's worktree fails 'no changes produced' even when --verify exits 0.

diff_base/diff_check units + wake worker gate (default on with --verify on a
worktree agent; --no-require-diff off; agent config; no-verify gate; escalate
climbs on an empty diff) + wakes display. Fake spawn (no fork) + real git in
temp dirs. All offline, no real backends.
"""
import importlib.machinery, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
AG = str(Path(__file__).resolve().parent.parent / "ag")

def load():
    """Fresh ag module + echo roster; no spawn (worker runs in-process)."""
    _l = importlib.machinery.SourceFileLoader("ag_reqdiff_t", AG)
    _s = importlib.util.spec_from_loader("ag_reqdiff_t", _l)
    m = importlib.util.module_from_spec(_s); _l.exec_module(m)
    td = Path(tempfile.mkdtemp(prefix="agrd-")); sdir = td / "st"; m.ensure_state(sdir)
    m.save_agents(sdir, [{"name": "a", "backend": "echo", "model": "default", "role": "sub"}])
    def fake(sd, agent, jid, sid=None, pre_fork=None):
        if pre_fork: pre_fork(sid or "s", None)
        return sid or "s"
    m._spawn_wake_session = fake
    return m, td, sdir

def repo(m, td):
    """Bare-ish git repo with one commit; -> (repo path, worktree info)."""
    repo = td / "repo"; repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def g(*a): return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=env)
    assert g("init", "-q", "-b", "main").returncode == 0
    (repo / "base.txt").write_text("v0\n")
    assert g("add", "-A").returncode == 0 and g("commit", "-qm", "init").returncode == 0
    wt, err, _ = m.wt_create("a", str(repo))
    assert not err, err
    return repo, wt

def wt_agent(m, sdir, wt):
    """Point agent a at its worktree (like agents add --worktree does)."""
    rows = json.loads((sdir / "agents.json").read_text())
    for r in rows:
        if r["name"] == "a":
            r["dir"] = wt["path"]; r["worktree"] = wt
    m.save_agents(sdir, rows)

def worker(m, sd, jid):
    """Run the wake worker body in-process (queued -> done/failed)."""
    return m.do__wake_run(m.build().parse_args(["--dir", str(sd), "_wake_run", jid]))

def job(m, sd, jid):
    return m._wake_read_job(sd, jid)

def run(m, fn, *argv):
    ns = m.build().parse_args(list(argv))
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = fn(ns)
    txt = buf.getvalue()
    data = json.loads(txt) if txt.strip().startswith("{") else None
    return rc or 0, data, txt, err.getvalue()

def wake(m, sd, *args):
    """One wake + in-process worker; -> final job dict."""
    rc, d, _, _ = run(m, m.do_wake, "--dir", str(sd), "--json", "wake", "a", "go", *args)
    assert rc == 0, d
    worker(m, sd, d["data"]["job"])
    return job(m, sd, d["data"]["job"])

class TDiffUnits(unittest.TestCase):
    def test_base_and_check_no_change(self):
        m, td, sd = load()
        repo_, wt = repo(m, td)
        base = m.diff_base(wt["path"])
        self.assertTrue(base and base["head"])
        d = m.diff_check(wt["path"], base)
        self.assertFalse(d["changed"]); self.assertEqual(d["base_sha"], base["head"])
        self.assertEqual(d["head_sha"], base["head"]); self.assertEqual(d["diff_stat"], "")

    def test_check_uncommitted_and_commit(self):
        m, td, sd = load()
        repo_, wt = repo(m, td)
        base = m.diff_base(wt["path"])
        Path(wt["path"], "made.txt").write_text("x\n")
        d = m.diff_check(wt["path"], base)
        self.assertTrue(d["changed"]); self.assertIn("1 uncommitted", d["diff_stat"])
        self.assertEqual(d["head_sha"], base["head"])  # working-tree change only
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "-C", str(wt["path"]), "add", "-A"], capture_output=True, env=env)
        subprocess.run(["git", "-C", str(wt["path"]), "commit", "-qm", "work"], capture_output=True, env=env)
        d = m.diff_check(wt["path"], base)
        self.assertTrue(d["changed"]); self.assertNotEqual(d["head_sha"], base["head"])
        self.assertIn("1 file changed", d["diff_stat"])

    def test_pre_existing_dirt_does_not_mask_noop(self):
        m, td, sd = load()
        repo_, wt = repo(m, td)
        Path(wt["path"], "dirty.txt").write_text("pre-existing\n")  # dirt before the wake
        base = m.diff_base(wt["path"])
        self.assertTrue(m.diff_check(wt["path"], None)["changed"])  # dirt alone reads as changed with no baseline
        d = m.diff_check(wt["path"], base)
        self.assertFalse(d["changed"])  # unchanged since the snapshot -> gate fails
        self.assertEqual(d["diff_stat"], "")

    def test_not_a_git_repo(self):
        m, td, sd = load()
        d = m.diff_check(td, None)
        self.assertFalse(d["changed"]); self.assertIsNone(d["head_sha"])

class TResolve(unittest.TestCase):
    def _row(self, **kw): return {"name": "a", **kw}

    def test_explicit_flag_wins(self):
        m, _, _ = load()
        self.assertFalse(m._require_diff(False, self._row(worktree={"path": "x"}), True))  # --no-require-diff
        self.assertTrue(m._require_diff(True, None, False))  # --require-diff

    def test_agent_config(self):
        m, _, _ = load()
        self.assertTrue(m._require_diff(None, self._row(wake_require_diff=True), False))
        self.assertFalse(m._require_diff(None, self._row(wake_require_diff=False), True))

    def test_default_needs_verify_and_worktree(self):
        m, _, _ = load()
        self.assertTrue(m._require_diff(None, self._row(worktree={"path": "x"}), True))
        self.assertFalse(m._require_diff(None, self._row(worktree={"path": "x"}), False))  # no verify -> off
        self.assertFalse(m._require_diff(None, self._row(), True))  # no worktree -> off
        self.assertFalse(m._require_diff(None, None, True))

class TWakeGate(unittest.TestCase):
    def test_default_on_no_changes_fails(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "true")
        self.assertEqual(j["status"], "failed"); self.assertEqual(j["error"], "no changes produced")
        self.assertEqual(j["backend_exit"], 1)
        self.assertTrue(j["require_diff"])
        self.assertTrue(j.get("base_sha")); self.assertEqual(j["head_sha"], j["base_sha"])
        self.assertEqual(j["diff_stat"], "")
        self.assertIn("[verify] exit=0", m.load_chat_log(sd, "a")[-1]["text"])  # verify still ran

    def test_changes_pass_with_diff_stat(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "echo work > made.txt")  # verify itself does the work
        self.assertEqual(j["status"], "done"); self.assertEqual(j["backend_exit"], 0)
        self.assertTrue(j["changed"] if "changed" in j else True)
        self.assertIn("1 uncommitted", j["diff_stat"])
        self.assertEqual(j["head_sha"], j["base_sha"])  # no commit, but uncommitted change
        self.assertTrue(j["base_sha"])

    def test_commit_counts(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify",
            "echo work > made.txt && git add -A && git -c user.email=t@t -c user.name=t commit -qm work")
        self.assertEqual(j["status"], "done")
        self.assertNotEqual(j["head_sha"], j["base_sha"])
        self.assertIn("1 file changed", j["diff_stat"])

    def test_no_require_diff_flag_off(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "true", "--no-require-diff")
        self.assertEqual(j["status"], "done"); self.assertFalse(j["require_diff"])
        self.assertIsNone(j.get("base_sha"))

    def test_require_diff_without_worktree_fails_loudly(self):
        m, td, sd = load()  # agent a has no worktree: explicit flag still gates
        j = wake(m, sd, "--verify", "true", "--require-diff")
        self.assertEqual(j["status"], "failed"); self.assertEqual(j["error"], "no changes produced")

    def test_default_off_without_verify(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd)  # no --verify: no gate
        self.assertEqual(j["status"], "done"); self.assertFalse(j["require_diff"])

    def test_agent_config_on_gates_without_verify(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        rows = json.loads((sd / "agents.json").read_text())
        for r in rows:
            if r["name"] == "a": r["wake_require_diff"] = True
        m.save_agents(sd, rows)
        j = wake(m, sd)
        self.assertEqual(j["status"], "failed"); self.assertEqual(j["error"], "no changes produced")
        self.assertTrue(j["require_diff"])
        # config off -> no gate even with --verify
        rows = json.loads((sd / "agents.json").read_text())
        for r in rows:
            if r["name"] == "a": r["wake_require_diff"] = False
        m.save_agents(sd, rows)
        j = wake(m, sd, "--verify", "true")
        self.assertEqual(j["status"], "done"); self.assertFalse(j["require_diff"])

    def test_agents_set_config_flag(self):
        m, td, sd = load()
        rc, _, _, err = run(m, m.do_agents, "--dir", str(sd), "agents", "set", "a", "--wake-require-diff", "on")
        self.assertEqual(rc, 0, err)
        self.assertTrue(next(r for r in m.load_agents(sd) if r["name"] == "a")["wake_require_diff"])
        rc, _, _, err = run(m, m.do_agents, "--dir", str(sd), "agents", "set", "a", "--wake-require-diff", "off")
        self.assertEqual(rc, 0, err)
        self.assertFalse(next(r for r in m.load_agents(sd) if r["name"] == "a")["wake_require_diff"])

    def test_wake_job_json_records_gate(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        rc, d, _, _ = run(m, m.do_wake, "--dir", str(sd), "--json", "wake", "a", "go", "--verify", "true")
        self.assertEqual(rc, 0)
        self.assertTrue(job(m, sd, d["data"]["job"])["require_diff"])  # resolved at enqueue

class TEscalate(unittest.TestCase):
    def test_empty_diff_climbs_then_fails(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "true", "--escalate", "m2,m3")
        self.assertEqual(j["status"], "failed"); self.assertEqual(j["error"], "no changes produced")
        esc = j["escalation"]
        self.assertEqual([e["model"] for e in esc], ["default", "m2", "m3"])
        self.assertTrue(all(e["verify_exit"] == 0 and not e["changed"] for e in esc))
        self.assertEqual(j["verify"]["exit"], 0)
        self.assertIn("[escalate] -> m2", " ".join(x["text"] for x in m.load_chat_log(sd, "a")))

    def test_changes_stop_the_climb(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "echo work > made.txt", "--escalate", "m2,m3")
        self.assertEqual(j["status"], "done")
        self.assertEqual([e["model"] for e in j["escalation"]], ["default"])  # first attempt passed the gate
        self.assertIn("1 uncommitted", j["diff_stat"])

class TWakesDisplay(unittest.TestCase):
    def test_text_and_json_show_diff_stat(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "echo work > made.txt")
        buf = io.StringIO()
        with redirect_stdout(buf):
            m.do_wakes(m.build().parse_args(["--dir", str(sd), "wakes"]))
        self.assertIn(f"diff=1 uncommitted", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            m.do_wakes(m.build().parse_args(["--dir", str(sd), "--json", "wakes"]))
        row = next(x for x in json.loads(buf.getvalue())["data"]["wakes"] if x["id"] == j["id"])
        self.assertEqual(row["base_sha"], j["base_sha"]); self.assertEqual(row["head_sha"], j["head_sha"])
        self.assertEqual(row["diff_stat"], j["diff_stat"])

    def test_failed_no_changes_shows_error(self):
        m, td, sd = load()
        _, wt = repo(m, td); wt_agent(m, sd, wt)
        j = wake(m, sd, "--verify", "true")
        buf = io.StringIO()
        with redirect_stdout(buf):
            m.do_wakes(m.build().parse_args(["--dir", str(sd), "wakes"]))
        self.assertIn("error=no changes produced", buf.getvalue())

if __name__ == "__main__": unittest.main()
