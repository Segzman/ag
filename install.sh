#!/bin/sh
# install.sh - local POSIX installer for ag (stdlib only, no deps).
# Copies sibling ag to $HOME/.local/bin/ag (--prefix DIR uses DIR/bin/ag).
# No sudo, no network, no backend installs, no shell-config changes.
set -eu

PREFIX=""
FORCE=0

err() {
    printf '%s\n' "$*" >&2
}

usage() {
    cat <<'EOF'
Usage: install.sh [--prefix DIR] [--force] [--help]

Install ag (sibling file in this directory) to $HOME/.local/bin/ag.
  --prefix DIR   install to DIR/bin/ag instead of $HOME/.local/bin/ag
  --force        replace existing target file or symlink (else refuse)
  --help, -h     show this help

Requires Python >= 3.9 (ag uses os.waitstatus_to_exitcode), macOS or Linux.
Existing directory at target is always refused. Install is atomic
(temp copy + chmod + mv). With --force a symlink is replaced itself,
its referent untouched.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            if [ $# -lt 2 ] || [ -z "${2:-}" ] || [ "${2#--}" != "$2" ]; then
                err "error: --prefix requires a DIR argument"
                usage >&2
                exit 1
            fi
            PREFIX="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --*)
            err "error: unknown option: $1"
            usage >&2
            exit 1
            ;;
        *)
            err "error: unexpected argument: $1"
            usage >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC="$SCRIPT_DIR/ag"

case "$(uname -s)" in
    Darwin|Linux) ;;
    *)
        err "error: unsupported OS: $(uname -s) (macOS/Linux only)"
        exit 1
        ;;
esac

if [ ! -f "$SRC" ]; then
    err "error: source not found or not a regular file: $SRC"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    err "error: python3 not found; need Python >= 3.9"
    exit 1
fi
if ! python3 -c 'import sys, os; sys.exit(0 if (sys.version_info >= (3, 9) and hasattr(os, "waitstatus_to_exitcode")) else 1)'; then
    err "error: Python >= 3.9 required (ag uses os.waitstatus_to_exitcode)"
    exit 1
fi

if [ -n "$PREFIX" ]; then
    PREFIX=${PREFIX%/}
    BINDIR="$PREFIX/bin"
else
    if [ -z "${HOME:-}" ]; then
        err "error: HOME unset; pass --prefix DIR"
        exit 1
    fi
    BINDIR="$HOME/.local/bin"
fi
TARGET="$BINDIR/ag"

mkdir -p "$BINDIR"

if [ -d "$TARGET" ]; then
    err "error: target is a directory, refusing: $TARGET"
    exit 1
fi
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    if [ "$FORCE" -eq 0 ]; then
        err "error: target exists, refusing (pass --force to replace): $TARGET"
        exit 1
    fi
    # No early deletion: atomic rename below replaces a regular file or
    # the symlink itself (referent untouched). Directories stay refused.
fi

TMP=""
cleanup() {
    if [ -n "${TMP:-}" ]; then
        rm -f "$TMP"
    fi
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM HUP
TMP=$(mktemp "$BINDIR/.ag.tmp.XXXXXX")
cp "$SRC" "$TMP"
chmod 755 "$TMP"
mv -f "$TMP" "$TARGET"
TMP=""
trap - EXIT INT TERM HUP

printf 'Installed ag to "%s"\n' "$TARGET"
case ":$PATH:" in
    *":$BINDIR:"*)
        printf 'On PATH: yes ("%s")\n' "$BINDIR"
        ;;
    *)
        printf 'On PATH: no. Add: export PATH="%s:$PATH"\n' "$BINDIR"
        ;;
esac
printf 'Example: "%s" --dir "$HOME/myproject/.agent" status\n' "$TARGET"
