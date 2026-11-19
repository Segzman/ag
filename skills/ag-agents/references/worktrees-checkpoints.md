# Worktrees, checkpoints, context handoff

## Isolation: worktree per agent

```sh
{{AG}} --dir <state> agents add w1 --backend claude --dir ~/proj --worktree [--base main]
{{AG}} --dir <state> agents rm w1 [--force]     # clean: removes worktree; --force drops dirty work; branch ag/w1 kept
```

Branch `ag/<name>`, worktree `~/proj.ag-wt/<name>`. Merge it yourself (`git merge ag/w1`); ag has no merge. Shared dir = agents clobber each other.

A worktree is not a sandbox. ag exports `$PWD` = the agent dir on every turn (opencode 2.x trusts `$PWD` over cwd, so a wake launched from the main checkout used to bind the session there), moves an opencode session bound to another dir back to the agent dir before resuming (`[session]` note in `chat log`), and fails any worktree-agent turn that changed the main checkout (`worktree escape: ... files`, exit 1, no retry; `AG_ESCAPE_GUARD=0` off, e.g. for a merge agent). opencode 2.x `plan`/`ro`/`edits`/profile turns run `--standalone` so the permission block applies (the shared service ignores per-turn config). `AG_SANDBOX=1` (macOS) additionally denies shell writes to the main checkout via `sandbox-exec`. Never put the main checkout's absolute path in a brief unless it must be read.

## Checkpoints, usage

If the agent dir is in a git repo, every non-slash turn is snapshotted pre/post (hidden refs `refs/ag/<agent>/<n>-pre|post`; your index/HEAD untouched; last 20; `AG_CHECKPOINT=0` disables).

```sh
{{AG}} --dir <state> chat checkpoints w | diff w [--turn N] [--stat]
{{AG}} --dir <state> chat revert w [--turn N]   # whole repo worktree back to before turn N; refuses if busy; undo ref refs/ag/<agent>/pre-revert
{{AG}} --dir <state> chat usage w               # last-turn + total provider tokens
```

Review `chat diff` before accepting work. Revert = whole repo.

## Context handoff

```sh
{{AG}} --dir <state> context init
{{AG}} --dir <state> context assign ui-a --scope ui --task "own chat panel" --acceptance "tests pass" --brief-file brief-a.md
{{AG}} --dir <state> context checkpoint ui-a --file ckpt-a.md
{{AG}} --dir <state> context show ui-b          # what B actually got + warnings
```

Same scope inherits prior `HANDOFF.md`; sibling scopes isolated. Native TUI gets no auto-inject. Auto-summaries: `compact show|config|run`.
