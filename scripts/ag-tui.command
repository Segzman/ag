#!/bin/sh
# ag-tui.command — macOS double-click launcher for this repo's ag TUI.
# Resolves its own repo root (spaces-safe), runs "$ROOT/ag --dir $SDIR tui"
# with exec so the terminal is restored on exit. Never copies ~/ag,
# never touches shell profiles or global config, no hard-coded usernames.
set -u

ROOT="$(cd "$(dirname "$0")" && cd .. && pwd)"
AG="$ROOT/ag"

if [ $# -gt 1 ]; then
  echo "ag-tui: expected at most one STATE_DIR argument, got $# (usage: ag-tui.command [STATE_DIR])" >&2
  exit 2
fi
if [ ! -f "$AG" ]; then
  echo "ag-tui: missing ag at $AG" >&2
  exit 1
fi
if [ ! -x "$AG" ]; then
  echo "ag-tui: not executable: $AG" >&2
  exit 1
fi

if [ $# -eq 1 ]; then
  SDIR="$1"
else
  SDIR="${AGENT_CLI_DIR:-$HOME/.agent}"
fi

case "$SDIR" in
  -*)
    echo "ag-tui: invalid STATE_DIR '$SDIR' (usage: ag-tui.command [STATE_DIR])" >&2
    exit 2
    ;;
  "" )
    echo "ag-tui: empty STATE_DIR (usage: ag-tui.command [STATE_DIR])" >&2
    exit 2
    ;;
esac

exec "$AG" --dir "$SDIR" tui
