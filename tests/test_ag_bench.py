#!/usr/bin/env python3
"""Tests for `ag bench` (run real tasks as benchmarks). Stdlib only, echo
backend + a temp git repo, no network, no real backends.
Run: python3 -m pytest -q tests/test_ag_bench.py

Design under test: `ag bench run SPEC.toml` runs each task x arm in a fresh
git worktree via `ag wake --verify`; hidden test files are staged outside the
worktree and copied in only by the verify wrapper at verify time. One JSONL
row per cell records pass/fail, ledger cost, tokens, wall time, escalations.
--keep-winner keeps the cheapest passing cell as branch bench/<task>-<arm>.
"""
import importlib.machinery, importlib.util, json, os, subprocess, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
AG = _REPO / "ag"
assert AG.exists(), f"missing ag binary: {AG}"

def _load_ag():
    loader = importlib.machinery.SourceFileLoader("ag_mod_bench", str(AG))
    spec = importlib.util.spec_from_loader("ag_mod_bench", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ag_mod_bench"] = mod
    loader.exec_module(mod)
    return mod

def run_ag(state, *args, timeout=300):
    env = dict(os.environ, AG_AUTO_UPDATE="0")
    return subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=env)

def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True)

def make_repo(td):
    repo = td / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo

def write_spec(td, repo, tasks, arms):
    def q(s): return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    out = []
    for t in tasks:
        out += ["[[task]]", f"name = {q(t['name'])}", f"repo = {q(repo)}",
                f"base = {q(t.get('base', 'main'))}", f"prompt = {q(t['prompt'])}",
                f"verify = {q(t['verify'])}"]
        if t.get("hidden"): out.append("hidden = [" + ", ".join(q(h) for h in t["hidden"]) + "]")
        out.append("")
    for a_ in arms:
        out += ["[[arm]]", f"name = {q(a_['name'])}", f"backend = {q(a_.get('backend', 'echo'))}"]
        if a_.get("model"): out.append(f"model = {q(a_['model'])}")
        if a_.get("escalate"): out.append("escalate = [" + ", ".join(q(m) for m in a_["escalate"]) + "]")
        out.append("")
    p = td / "spec.toml"
    p.write_text("\n".join(out))
    return p

def read_rows(path):
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]

# ---- pure/unit: spec parsing (incl. the no-tomllib fallback) + report ----

def test_spec_load_and_toml_fallback(tmp_path):
    ag = _load_ag()
    repo = make_repo(tmp_path)
    spec = write_spec(tmp_path, repo,
        [{"name": "greet", "prompt": "hi", "verify": "true", "hidden": ["hidden/test_hidden.py"]}],
        [{"name": "echo1", "backend": "echo", "escalate": ["default"]}])
    doc, err = ag.bench_load_spec(str(spec))
    assert err is None, err
    assert doc["tasks"][0]["name"] == "greet"
    assert doc["tasks"][0]["hidden"] == [{"src": "hidden/test_hidden.py", "dest": "hidden/test_hidden.py"}]
    assert doc["arms"][0]["escalate"] == ["default"]
    fb = ag._bench_toml_fallback(spec.read_text())
    assert fb["task"][0]["verify"] == "true" and fb["arm"][0]["backend"] == "echo", fb
    bad = tmp_path / "bad.toml"
    bad.write_text('[[task]]\nname = "x"\n')
    doc2, err2 = ag.bench_load_spec(str(bad))
    assert doc2 is None and "invalid bench spec" in err2, err2
    print("ok test_spec_load_and_toml_fallback")

def test_report_md(tmp_path):
    ag = _load_ag()
    rows = [
        {"task": "t1", "arm": "a1", "backend": "echo", "model": "default", "effort": "",
         "status": "pass", "pass": True, "cost_usd": 0.5, "tokens": 10, "wall_s": 3.0, "escalations": 0},
        {"task": "t1", "arm": "a2", "backend": "echo", "model": "default", "effort": "",
         "status": "pass", "pass": True, "cost_usd": 0.25, "tokens": 5, "wall_s": 9.0,
         "escalations": 1, "kept_branch": "bench/t1-a2"},
        {"task": "t1", "arm": "a3", "backend": "echo", "model": "default", "effort": "",
         "status": "parked", "pass": False, "cost_usd": None, "tokens": 0, "wall_s": 1.0, "escalations": 0},
    ]
    md = ag.bench_report_md(rows)
    assert "| t1 | a2 | echo/default | pass | $0.2500 | 5 | 9.0s | 1 |" in md, md
    assert "winner t1: a2 $0.2500 (kept branch bench/t1-a2)" in md, md
    rp = tmp_path / "r.jsonl"
    rp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    p = run_ag(tmp_path / "state", "bench", "report", str(rp))
    assert p.returncode == 0 and "winner t1: a2" in p.stdout, (p.stdout, p.stderr)
    print("ok test_report_md")

def test_bench_bad_spec_cli(tmp_path):
    p = run_ag(tmp_path / "state", "bench", "run", str(tmp_path / "nope.toml"))
    assert p.returncode == 1 and "cannot read spec" in (p.stderr + p.stdout), (p.stdout, p.stderr)
    p = run_ag(tmp_path / "state", "bench")
    assert p.returncode == 1 and "no bench subcommand" in (p.stderr + p.stdout), (p.stdout, p.stderr)
    print("ok test_bench_bad_spec_cli")

# ---- end to end: echo backend, temp git repo, no network ----

def test_bench_run_pass_hidden_and_keep_winner(tmp_path):
    repo = make_repo(tmp_path)
    hid = tmp_path / "hidden"
    hid.mkdir()
    (hid / "test_hidden.py").write_text("def test_hidden():\n    assert True\n")
    spec = write_spec(tmp_path, repo,
        [{"name": "greet", "prompt": "say hi",
          "verify": "test -f hidden/test_hidden.py && grep -q hello hello.txt",
          "hidden": ["hidden/test_hidden.py"]}],
        [{"name": "echo1", "backend": "echo"}])
    out = tmp_path / "results.jsonl"
    p = run_ag(tmp_path / "state", "bench", "run", str(spec), "--out", str(out), "--keep-winner")
    assert p.returncode == 0, (p.stdout, p.stderr)
    rows = read_rows(out)
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["status"] == "pass" and r["pass"] is True and r["verify_exit"] == 0, r
    assert r["job"] and r["wall_s"] > 0 and r["escalations"] == 0, r
    assert "cost_usd" in r and "tokens" in r, r
    # winner kept as a branch; the hidden file was copied in only at verify
    # time and never committed, and the scratch worktree/branch are gone
    assert r.get("kept_branch") == "bench/greet-echo1", r
    branches = _git(repo, "branch", "--list").stdout
    assert "bench/greet-echo1" in branches and "bench/run-" not in branches, branches
    tree = _git(repo, "ls-tree", "-r", "--name-only", "bench/greet-echo1").stdout
    assert "hello.txt" in tree and "test_hidden.py" not in tree, tree
    assert not Path(r["worktree"]).exists(), r["worktree"]
    print("ok test_bench_run_pass_hidden_and_keep_winner")

def test_bench_run_fail_and_escalation(tmp_path):
    repo = make_repo(tmp_path)
    spec = write_spec(tmp_path, repo,
        [{"name": "impossible", "prompt": "do nothing", "verify": "test -f feature.txt"}],
        [{"name": "echo1", "backend": "echo", "escalate": ["default"]}])
    out = tmp_path / "results.jsonl"
    p = run_ag(tmp_path / "state", "bench", "run", str(spec), "--out", str(out), "--jobs", "1")
    assert p.returncode == 0, (p.stdout, p.stderr)
    rows = read_rows(out)
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["status"] == "fail" and r["pass"] is False and r["verify_exit"] != 0, r
    assert r["escalations"] >= 1 and len(r["escalation"]) >= 2, r
    assert "kept_branch" not in r, r
    branches = _git(repo, "branch", "--list").stdout
    assert "bench/" not in branches, branches  # nothing kept, nothing pushed
    print("ok test_bench_run_fail_and_escalation")

if __name__ == "__main__":
    import tempfile
    for fn in (test_spec_load_and_toml_fallback, test_report_md, test_bench_bad_spec_cli,
               test_bench_run_pass_hidden_and_keep_winner, test_bench_run_fail_and_escalation):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    print("all bench tests passed")
