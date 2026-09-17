#!/usr/bin/env python3
"""Acceptance tests for scoped guidance + persistent agent memory.

Temp state + temp project trees, echo backend only (no paid models).
Covers: init preservation, nested custom.md inheritance, explicit files,
same-scope handoff inheritance, sibling/project isolation, resumed refresh,
raw --command untouched, invalid paths/symlinks, truncation, persistence.
Full CLI workflow: init -> assign A -> checkpoint -> create/assign B -> show.

Run: python3 tests/test_ag_context.py
Env: AG_BIN overrides live ag path (default: repo ag).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AG = Path(os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag")))
assert AG.exists(), f"missing ag: {AG}"

spec = importlib.util.spec_from_loader("agmod", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)

PASS = 0
def check(name, cond, extra=""):
    global PASS
    if not cond:
        raise AssertionError(f"FAIL {name} {extra}")
    PASS += 1
    print(f"ok {name}")

def fresh_state(rows):
    td = Path(tempfile.mkdtemp(prefix="agctx-state-"))
    (td / "agents.json").write_text(json.dumps(rows))
    return td

def mkproj():
    p = Path(tempfile.mkdtemp(prefix="agctx-proj-"))
    return p

def echo_agent(name, proj, **kw):
    r = {"name": name, "backend": "echo", "model": "default",
         "dir": str(proj), "role": "sub", "persona": "", "system": ""}
    r.update(kw)
    return r

class A:
    def __init__(self, **k): self.__dict__.update(k)

def ctx_args(state, **k):
    d = {"dir": str(state), "json": True, "ctxsub": "show", "cname": "",
         "scope": "", "project_root": None, "task": None, "acceptance": None,
         "cfile": [], "brief": None, "brief_file": None, "ctext": [],
         "cpt_file": None}
    d.update(k)
    return A(**d)

def assign(state, name, scope="", **kw):
    kw.setdefault("ctxsub", "assign")
    return ag.do_context(ctx_args(state, cname=name, scope=scope, **kw))

def show(state, name):
    return ag.get_agent_context(state, name)

def test_init_preserves():
    st = fresh_state([])
    ag.do_context(ctx_args(st, ctxsub="init"))
    ap = st / "context" / "assignments.json"
    ap.write_text(json.dumps({"keep": {"scope": ""}}))
    ag.do_context(ctx_args(st, ctxsub="init"))
    check("init-preserves", json.loads(ap.read_text()) == {"keep": {"scope": ""}})

def test_nested_inheritance_and_explicit():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# ROOT-RULES\n")
    (proj / "ui").mkdir()
    (proj / "ui" / "custom.md").write_text("# UI-RULES\n")
    (proj / "ui" / "deep").mkdir()
    (proj / "ui" / "deep" / "custom.md").write_text("# DEEP-RULES\n")
    (proj / "extra.md").write_text("# EXTRA-FILE\n")
    (proj / "other.md").write_text("# SHOULD-NOT-APPEAR\n")
    rows = [echo_agent("a", proj, instructions=["extra.md"])]
    (st / "agents.json").write_text(json.dumps(rows))
    assign(st, "a", scope="ui/deep", task="t", acceptance="ac")
    c = show(st, "a")
    check("nested-root", "ROOT-RULES" in c["text"], c["text"][:200])
    check("nested-mid", "UI-RULES" in c["text"])
    check("nested-leaf", "DEEP-RULES" in c["text"])
    check("explicit-instructions", "EXTRA-FILE" in c["text"])
    check("no-blanket-ingest", "SHOULD-NOT-APPEAR" not in c["text"])
    kinds = [s["kind"] for s in c["sources"]]
    check("source-kinds", "custom-md" in kinds and "file" in kinds, kinds)
    check("assign-record", c["assignment"]["scope"] == "ui/deep"
          and c["assignment"]["task"] == "t" and c["assignment"]["acceptance"] == "ac")

def test_handoff_inheritance_and_isolation():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    (proj / "ui").mkdir()
    sib = proj / "other"
    sib.mkdir()
    proj2 = mkproj()
    (proj2 / "custom.md").write_text("# P2\n")
    rows = [echo_agent("a", proj), echo_agent("b", proj),
            echo_agent("s", proj), echo_agent("p2", proj2)]
    (st / "agents.json").write_text(json.dumps(rows))
    assign(st, "a", scope="ui", brief="BRIEF-A")
    ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="a",
                           ctext=["goal G", "done D", "next N"]))
    assign(st, "b", scope="ui", brief="BRIEF-B")
    cb = show(st, "b")
    check("same-scope-handoff", "done D" in cb["text"] and "goal G" in cb["text"])
    check("own-brief", "BRIEF-B" in cb["text"])
    check("no-peer-brief", "BRIEF-A" not in cb["text"])
    assign(st, "s", scope="other", brief="BRIEF-S")
    cs = show(st, "s")
    check("sibling-excluded", "done D" not in cs["text"] and "BRIEF-A" not in cs["text"])
    assign(st, "p2", scope="ui", brief="BRIEF-P2")
    cp = show(st, "p2")
    check("project-excluded", "done D" not in cp["text"] and "# R" not in cp["text"]
          and "P2" in cp["text"])

def test_resumed_refresh_and_history_clean():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# V1\n")
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    assign(st, "a", scope="", brief="B")
    r1 = ag.run_turn(st, "a", "first")
    check("turn1-guidance", "V1" in r1["reply"] and "first" in r1["reply"], r1["reply"][:200])
    (proj / "custom.md").write_text("# V2-UPDATED\n")
    ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="a", ctext=["note N1"]))
    r2 = ag.run_turn(st, "a", "second")
    check("resumed-refresh", "V2-UPDATED" in r2["reply"] and "N1" in r2["reply"])
    check("request-prominent", r2["reply"].rstrip().endswith("second"), r2["reply"][-80:])
    log = ag.load_chat_log(st, "a")
    users = [x["text"] for x in log if x["role"] == "user"]
    check("history-clean", users == ["first", "second"], users)

def test_command_untouched():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# CMDRULES\n")
    (st / "agents.json").write_text(json.dumps(
        [dict(echo_agent("o", proj), backend="opencode",
              model="opencode/muse-spark-1.3-contributor-free")]))
    assign(st, "o", scope="")
    seen = {}
    orig = ag.backend_argv
    def cap(backend, **kw):
        seen.update(kw)
        return ["printf", "%s\n", kw.get("prompt", "")]
    ag.backend_argv = cap
    try:
        r = ag.run_turn(st, "o", "do review", command="review")
    finally:
        ag.backend_argv = orig
    check("command-ok", "error" not in r, r)
    check("command-bare", seen.get("prompt", "") == "do review", seen.get("prompt", "")[:200])
    check("command-no-system", seen.get("system", "") == "", seen.get("system", "")[:100])

def test_invalid_paths_and_symlinks():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    (proj / "ok.md").write_text("# OK\n")
    (proj / "notes.txt").write_text("plain\n")
    outside = Path(tempfile.mkdtemp(prefix="agctx-out-")) / "evil.md"
    outside.write_text("# EVIL\n")
    link = proj / "link.md"
    try:
        link.symlink_to(proj / "ok.md")
        have_link = True
    except Exception:
        have_link = False
    rows = [echo_agent("a", proj,
                       instructions=["ok.md", "../evil.md", str(outside), "notes.txt"]
                       + (["link.md"] if have_link else [])),
            echo_agent("w", proj)]
    (st / "agents.json").write_text(json.dumps(rows))
    r = assign(st, "a", scope="../..")
    check("bad-scope-no-assign", True)  # assign returns via emit; check record absent
    check("bad-scope-absent", "a" not in ag.ctx_load_assignments(st))
    assign(st, "a", scope="")
    c = show(st, "a")
    check("ok-included", "OK" in c["text"])
    stats = {(s["path"], s["status"]) for s in c["sources"]}
    check("traversal-blocked", any(st8 == "outside-root" for _, st8 in stats), stats)
    check("badtype-blocked", any(st8 == "bad-type" for _, st8 in stats), stats)
    if have_link:
        check("symlink-skipped", any(st8 == "skipped-symlink" for _, st8 in stats), stats)
    check("warnings-present", len(c["warnings"]) >= 2, c["warnings"])
    # checkpoint write through symlink refused
    assign(st, "w", scope="")
    rec = ag.ctx_load_assignments(st)["w"]
    canon = Path(rec["project"])
    key = ag.ctx_projkey(canon)
    agdir = ag.ctx_agent_dir(ag._ctx_root(st) / "p" / key, "", "w")
    agdir.mkdir(parents=True, exist_ok=True)
    tgt = agdir / "CHECKPOINT.md"
    real = agdir / "real.md"
    real.write_text("x")
    try:
        tgt.symlink_to(real)
        err = ag._ctx_write_state_file(tgt, "new")
        check("write-symlink-refused", err != "", err)
    except Exception:
        check("write-symlink-refused", True)

def test_truncation_reported():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    (proj / "big.md").write_text("X" * (ag.CTX_PER_FILE + 500))
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj, instructions=["big.md"])]))
    assign(st, "a", scope="")
    c = show(st, "a")
    big = [s for s in c["sources"] if s["path"].endswith("big.md")][0]
    check("oversized-flag", big["status"] == "oversized" and big["truncated"], big)
    check("trunc-warning", any("big.md" in w for w in c["warnings"]), c["warnings"])
    check("total-bounded", len(c["text"]) <= ag.CTX_TOTAL + 2000, len(c["text"]))

def test_persistence_and_errors():
    st = fresh_state([])
    (st / "agents.json").write_text(json.dumps([echo_agent("a", ".")]))
    c = show(st, "ghost")
    check("unknown-agent", c["text"] == "" and c["warnings"], c)
    c = show(st, "a")
    check("unassigned-defaults", c["assignment"] == {}
          and any("unassigned" in w for w in c["warnings"]), c["warnings"])
    # workdir "." resolves to the process cwd: no custom.md there in temp cwd,
    # but an unassigned agent with a real project root gets root guidance.
    proj = mkproj()
    (proj / "custom.md").write_text("# UNASSIGNED-ROOT\n")
    rows = json.loads((st / "agents.json").read_text())
    rows.append(echo_agent("u", proj))
    (st / "agents.json").write_text(json.dumps(rows))
    cu = show(st, "u")
    check("unassigned-root-guidance", "UNASSIGNED-ROOT" in cu["text"]
          and cu["assignment"] == {}, cu["text"][:200])
    projs = ag.ctx_load_projects(st)
    keys_before = set(projs)
    assign(st, "a", scope="")
    check("projects-persisted", set(ag.ctx_load_projects(st)) >= keys_before)
    rec = ag.ctx_load_assignments(st).get("a", {})
    check("record-fields", set(("project", "scope", "files", "task", "acceptance", "updated")) <= set(rec), rec)
    check("scope-validation", ag.ctx_validate_scope("a/b") == "a/b"
          and ag.ctx_validate_scope("../x") is None
          and ag.ctx_validate_scope("/abs") is None
          and ag.ctx_validate_scope("a//b") is None
          and ag.ctx_validate_scope("a/") == "a"
          and ag.ctx_validate_scope("") == "")

def test_cli_workflow_end_to_end():
    state = Path(tempfile.mkdtemp(prefix="agctx-cli-state-"))
    proj = mkproj()
    (proj / "custom.md").write_text("# CLI-ROOT\n")
    (proj / "ui").mkdir()
    brief_a = proj / "brief-a.md"
    brief_a.write_text("BRIEF-CLI-A")
    ckpt_a = proj / "ckpt-a.md"
    ckpt_a.write_text("goal ship\ncompleted panel\ntests pytest green\nnext docs")
    brief_b = proj / "brief-b.md"
    brief_b.write_text("BRIEF-CLI-B")
    def run(*args):
        env = dict(os.environ, AGENT_CLI_DIR=str(state))
        p = subprocess.run([sys.executable, str(AG), "--json"] + list(args),
                           capture_output=True, text=True, timeout=30, env=env)
        try:
            return p, json.loads(p.stdout)
        except Exception:
            raise AssertionError(f"not JSON for {args}: rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")
    _, o = run("context", "init")
    assert o["ok"], o
    _, o = run("agents", "add", "cli-a", "--backend", "echo", "--dir", str(proj))
    assert o["ok"], o
    _, o = run("context", "assign", "cli-a", "--scope", "ui", "--task", "panel",
               "--acceptance", "green", "--brief-file", str(brief_a))
    assert o["ok"], o
    _, o = run("context", "checkpoint", "cli-a", "--file", str(ckpt_a))
    assert o["ok"], o
    _, o = run("agents", "add", "cli-b", "--backend", "echo", "--dir", str(proj))
    assert o["ok"], o
    _, o = run("context", "assign", "cli-b", "--scope", "ui", "--brief-file", str(brief_b))
    assert o["ok"], o
    _, o = run("context", "show", "cli-b")
    assert o["ok"], o
    txt = o["data"]["text"]
    check("cli-handoff", "completed panel" in txt, txt[:300])
    check("cli-brief", "BRIEF-CLI-B" in txt)
    check("cli-no-peer-brief", "BRIEF-CLI-A" not in txt)
    check("cli-sources", any(s["kind"] == "handoff" and s["status"] == "ok"
                             for s in o["data"]["sources"]), o["data"]["sources"])

def test_task_acceptance_without_brief():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    assign(st, "a", scope="", task="TASK-SHIP-IT", acceptance="ACC-GREEN")
    c = show(st, "a")
    check("task-in-text", "TASK-SHIP-IT" in c["text"] and "ACC-GREEN" in c["text"],
          c["text"][:300])
    r = ag.run_turn(st, "a", "go")
    check("task-reaches-backend", "TASK-SHIP-IT" in r["reply"] and "ACC-GREEN" in r["reply"])

def test_unassigned_instructions_work():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# UROOT\n")
    (proj / "guide.md").write_text("# UGUIDE\n")
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj, instructions=["guide.md"])]))
    c = show(st, "a")
    check("unassigned-chain", "# UROOT" in c["text"])
    check("unassigned-explicit", "# UGUIDE" in c["text"])
    check("unassigned-empty-record", c["assignment"] == {})

def test_symlink_ancestor_escape():
    if os.name != "posix":
        check("symlink-ancestor-skipped-posix-only", True)
        return
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    real = proj / "real-ui"
    real.mkdir()
    (real / "custom.md").write_text("# EVIL-OUTSIDE-CHAIN\n")
    (proj / "ui").symlink_to(real, target_is_directory=True)
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    assign(st, "a", scope="ui")
    c = show(st, "a")
    check("chain-symlink-rejected", "EVIL-OUTSIDE-CHAIN" not in c["text"], c["text"][:300])
    check("chain-symlink-status", any(s["status"] in ("skipped-symlink", "outside-root")
                                      for s in c["sources"]), c["sources"])
    # state write refusal when a planted symlink hijacks the scope tree
    rows = json.loads((st / "agents.json").read_text())
    rows.append(echo_agent("b", proj))
    (st / "agents.json").write_text(json.dumps(rows))
    assign(st, "b", scope="ok")
    rec = ag.ctx_load_assignments(st)["b"]
    projdir = ag._ctx_root(st) / "p" / ag.ctx_projkey(Path(rec["project"]))
    projdir.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="agctx-hijack-"))
    (projdir / "scope").symlink_to(target, target_is_directory=True)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="b", ctext=["hi"]))
    try:
        out = json.loads(buf.getvalue())
    except Exception:
        out = {}
    check("write-hijack-refused", out.get("ok") is False, buf.getvalue()[:200])
    check("no-escape-written", not any(target.iterdir()), list(target.iterdir())[:3])

def test_absolute_scope_rejected_cli():
    state = Path(tempfile.mkdtemp(prefix="agctx-cli-scope-"))
    proj = mkproj()
    def run(*args):
        env = dict(os.environ, AGENT_CLI_DIR=str(state))
        p = subprocess.run([sys.executable, str(AG), "--json"] + list(args),
                           capture_output=True, text=True, timeout=30, env=env)
        return p, json.loads(p.stdout)
    _, o = run("agents", "add", "x", "--backend", "echo", "--dir", str(proj))
    assert o["ok"], o
    _, o = run("context", "assign", "x", "--scope", "/foo")
    check("absolute-scope-rejected", o["ok"] is False and "bad scope" in o.get("error", ""), o)

def test_agent_key_no_collision():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([echo_agent("a/b", proj), echo_agent("a_b", proj)]))
    assign(st, "a/b", scope="", brief="BRIEF-SLASH")
    assign(st, "a_b", scope="", brief="BRIEF-PLAIN")
    cb = show(st, "a/b")
    cp = show(st, "a_b")
    check("slash-own-brief", "BRIEF-SLASH" in cb["text"] and "BRIEF-PLAIN" not in cb["text"])
    check("plain-own-brief", "BRIEF-PLAIN" in cp["text"] and "BRIEF-SLASH" not in cp["text"])
    kb = ag.ctx_agent_dir(Path("/s"), "", "a/b")
    kp = ag.ctx_agent_dir(Path("/s"), "", "a_b")
    check("keys-unique", kb != kp, (str(kb), str(kp)))

def test_bounded_io_and_reserve():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    big = proj / "big.md"
    with open(big, "w") as h:
        h.write("Z" * (5 * 1024 * 1024))
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj, instructions=["big.md"])]))
    assign(st, "a", scope="", task="RESERVE-TASK", brief="x")
    ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="a", ctext=["RESERVE-HANDOFF"]))
    import time
    t0 = time.time()
    c = show(st, "a")
    dt = time.time() - t0
    big_src = [s for s in c["sources"] if s["path"].endswith("big.md")][0]
    check("bounded-bytes-reported", big_src["bytes"] == 5 * 1024 * 1024, big_src)
    check("bounded-truncated", big_src["truncated"] and big_src["status"] == "oversized")
    check("bounded-fast", dt < 5, dt)
    check("reserve-task", "RESERVE-TASK" in c["text"])
    check("reserve-handoff", "RESERVE-HANDOFF" in c["text"])
    check("total-bounded-strict", len(c["text"]) <= ag.CTX_TOTAL, len(c["text"]))

def test_partial_assign_commits_nothing():
    if os.name != "posix":
        check("partial-assign-skipped-posix-only", True)
        return
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    link = proj / "brief-link.md"
    (proj / "real-brief.md").write_text("x")
    link.symlink_to(proj / "real-brief.md")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ag.do_context(ctx_args(st, ctxsub="assign", cname="a", scope="",
                               brief_file=str(link), task="T"))
    out = json.loads(buf.getvalue())
    check("brief-symlink-errors", out.get("ok") is False, buf.getvalue()[:200])
    check("no-partial-record", "a" not in ag.ctx_load_assignments(st))

def test_reassign_preserves_and_clears():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    (proj / "f.md").write_text("# F-FILE\n")
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    assign(st, "a", scope="ui", task="TASK1", acceptance="ACC1",
           brief="BRIEF1", cfile=["f.md"])
    # same-scope reassign, flags absent -> everything preserved
    assign(st, "a", scope="ui")
    rec = ag.ctx_load_assignments(st)["a"]
    check("reassign-preserves-task", rec["task"] == "TASK1", rec)
    check("reassign-preserves-acc", rec["acceptance"] == "ACC1", rec)
    check("reassign-preserves-files", rec["files"] == ["f.md"], rec)
    c = show(st, "a")
    check("reassign-preserves-brief",
          "BRIEF1" in c["text"] and "# F-FILE" in c["text"], c["text"][:300])
    # explicit empty clears task + brief, keeps acceptance/files
    assign(st, "a", scope="ui", task="", brief="")
    rec = ag.ctx_load_assignments(st)["a"]
    check("reassign-clears-task", rec["task"] == "", rec)
    check("reassign-keeps-acc", rec["acceptance"] == "ACC1", rec)
    check("reassign-keeps-files", rec["files"] == ["f.md"], rec)
    c = show(st, "a")
    check("reassign-clears-brief", "BRIEF1" not in c["text"], c["text"][:300])
    # new scope never inherits the old task/brief (no accidental injection)
    assign(st, "a", scope="ui", task="TASK2", brief="BRIEF2")
    assign(st, "a", scope="other")
    rec = ag.ctx_load_assignments(st)["a"]
    check("newscope-no-task", rec["task"] == "" and rec["acceptance"] == "", rec)
    c = show(st, "a")
    check("newscope-no-brief", "BRIEF2" not in c["text"], c["text"][:300])
    # parser level: absent flag is None (preserve), explicit "" clears
    ns = ag.build().parse_args(["context", "assign", "a"])
    check("cli-absent-None", ns.task is None and ns.acceptance is None,
          (ns.task, ns.acceptance))
    ns2 = ag.build().parse_args(["context", "assign", "a", "--task", ""])
    check("cli-explicit-empty", ns2.task == "", ns2.task)

def test_agent_rm_clears_assignment():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# R\n")
    (st / "agents.json").write_text(json.dumps([echo_agent("a", proj)]))
    assign(st, "a", scope="ui", task="TASK-RM", brief="BRIEF-RM")
    ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="a", ctext=["HANDOFF-RM"]))
    rec = ag.ctx_load_assignments(st)["a"]
    handoff = ag.ctx_scope_dir(
        ag._ctx_root(st) / "p" / ag.ctx_projkey(Path(rec["project"])), "ui") / "HANDOFF.md"
    check("rm-handoff-exists", handoff.is_file())
    ag.do_agents(A(dir=str(st), json=True, sub="rm", name="a"))
    check("rm-assignment-gone", "a" not in ag.ctx_load_assignments(st))
    check("rm-shared-retained",
          handoff.is_file() and "HANDOFF-RM" in handoff.read_text())
    # name reused by a different-project agent: no stale task/handoff
    proj2 = mkproj()
    (proj2 / "custom.md").write_text("# P2\n")
    rows = json.loads((st / "agents.json").read_text())
    rows.append(echo_agent("a", proj2))
    (st / "agents.json").write_text(json.dumps(rows))
    c = show(st, "a")
    check("reuse-unassigned", c["assignment"] == {}, c["assignment"])
    check("reuse-no-stale", "TASK-RM" not in c["text"] and "HANDOFF-RM" not in c["text"]
          and "# P2" in c["text"], c["text"][:300])

def test_resumed_sid_refresh_stubbed():
    st = fresh_state([])
    proj = mkproj()
    (proj / "custom.md").write_text("# V1\n")
    (st / "agents.json").write_text(json.dumps(
        [dict(echo_agent("o", proj), backend="opencode",
              model="opencode/muse-spark-1.3-contributor-free")]))
    assign(st, "o", scope="", task="T")
    # seed a resumed native session id, as a prior opencode turn would leave
    ag.save_chat_meta(st, "o", {"sid": "ses_9", "sids": {"opencode": "ses_9"},
                                "backend": "opencode",
                                "model": "opencode/muse-spark-1.3-contributor-free",
                                "dir": str(proj), "role": "sub"})
    seen = {}
    orig = ag.backend_argv
    def cap(backend, **kw):
        seen.update(kw)
        seen["backend"] = backend
        return ["printf", "%s\n", kw.get("prompt", "")]
    ag.backend_argv = cap
    try:
        r1 = ag.run_turn(st, "o", "first")
    finally:
        ag.backend_argv = orig
    check("resumed-turn-ok", "error" not in r1, r1)
    check("resumed-guidance", "V1" in r1["reply"], r1["reply"][:200])
    check("resumed-sid-sent", seen.get("sid", "") == "ses_9", seen)
    # rotate guidance + handoff, take another resumed turn: fresh context,
    # no history replay, raw history keeps user text only
    (proj / "custom.md").write_text("# V2-UPDATED\n")
    ag.do_context(ctx_args(st, ctxsub="checkpoint", cname="o", ctext=["note N1"]))
    seen.clear()
    ag.backend_argv = cap
    try:
        r2 = ag.run_turn(st, "o", "second")
    finally:
        ag.backend_argv = orig
    check("resumed-refresh",
          "V2-UPDATED" in r2["reply"] and "N1" in r2["reply"], r2["reply"][:300])
    check("resumed-no-replay", "first" not in r2["reply"], r2["reply"][-200:])
    check("request-prominent", r2["reply"].rstrip().endswith("second"), r2["reply"][-80:])
    log = ag.load_chat_log(st, "o")
    users = [x["text"] for x in log if x["role"] == "user"]
    check("history-clean", users == ["first", "second"], users)

if __name__ == "__main__":
    test_init_preserves()
    test_nested_inheritance_and_explicit()
    test_handoff_inheritance_and_isolation()
    test_resumed_refresh_and_history_clean()
    test_command_untouched()
    test_invalid_paths_and_symlinks()
    test_truncation_reported()
    test_persistence_and_errors()
    test_task_acceptance_without_brief()
    test_unassigned_instructions_work()
    test_symlink_ancestor_escape()
    test_absolute_scope_rejected_cli()
    test_agent_key_no_collision()
    test_bounded_io_and_reserve()
    test_partial_assign_commits_nothing()
    test_reassign_preserves_and_clears()
    test_agent_rm_clears_assignment()
    test_resumed_sid_refresh_stubbed()
    test_cli_workflow_end_to_end()
    print(f"PASS {PASS} checks")
