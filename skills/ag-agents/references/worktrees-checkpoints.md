# Worktrees, checkpoints, context handoff

## Isolation: worktree per agent

```sh
{{AG}} --dir <state> agents add w1 --backend claude --dir ~/proj --worktree [--base main]
{{AG}} --dir <state> agents rm w1 [--force]     # clean: removes worktree; --force drops dirty work; branch ag/w1 kept
```

Branch `ag/<name>`, worktree `~/proj.ag-wt/<name>`. Merge it yourself (`git merge ag/w1`); ag has no merge. Shared dir = agents clobber each other.

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
