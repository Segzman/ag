#!/bin/sh
# setup-muse.sh - run ON the Muse Linux host from an ag checkout.
# Installs ag (sibling file, no network) + OpenCode binary (official
# installer, user-scoped). No sudo, no global writes, no shell-rc edits,
# no credentials, no SSH config changes. Authenticate backends separately.
set -eu

PREFIX=""
FORCE=0
SKIP_OPENCODE=0
OPENCODE_URL="${OPENCODE_INSTALL_URL:-https://opencode.ai/install}"

err() { printf '%s\n' "$*" >&2; }

usage() {
    cat <<'EOF'
Usage: setup-muse.sh [--prefix DIR] [--force] [--skip-opencode] [--help]

Run ON the Muse host from an ag checkout. Installs:
  1. ag via ./install.sh (single-file copy, no network, no sudo)
  2. OpenCode binary via the official installer with --no-modify-path
     (user-scoped to ~/.opencode/bin; shell rc files untouched)

  --prefix DIR     pass through to install.sh (ag to DIR/bin/ag)
  --force          pass through to install.sh (replace existing ag)
  --skip-opencode  install ag only
  --help, -h       show this help

Env: OPENCODE_INSTALL_URL overrides the installer URL (tests only).

Never writes credentials, SSH config, or shell rc files. The script
installs the OpenCode binary only; authenticate backends (e.g. API keys,
opencode auth) separately after installing.
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
        --force) FORCE=1; shift ;;
        --skip-opencode) SKIP_OPENCODE=1; shift ;;
        --help|-h) usage; exit 0 ;;
        --*) err "error: unknown option: $1"; usage >&2; exit 1 ;;
        *) err "error: unexpected argument: $1"; usage >&2; exit 1 ;;
    esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

case "$(uname -s)" in
    Linux|Darwin) ;;
    *) err "error: unsupported OS: $(uname -s) (Linux expected on Muse)"; exit 1 ;;
esac

if [ -z "${HOME:-}" ] && [ -z "$PREFIX" ]; then
    err "error: HOME unset; pass --prefix DIR"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    err "error: python3 not found; need Python >= 3.9"
    exit 1
fi
if ! python3 -c 'import sys, os; sys.exit(0 if (sys.version_info >= (3, 9) and hasattr(os, "waitstatus_to_exitcode")) else 1)'; then
    err "error: Python >= 3.9 required"
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    err "error: git not found"
    exit 1
fi

# Preserve args exactly (spaces safe): rebuild "$@" for install.sh.
set --
if [ -n "$PREFIX" ]; then set -- "$@" --prefix "$PREFIX"; fi
if [ "$FORCE" -eq 1 ]; then set -- "$@" --force; fi
sh "$REPO_DIR/install.sh" "$@"

if [ -n "$PREFIX" ]; then
    AG_BIN="${PREFIX%/}/bin/ag"
else
    AG_BIN="$HOME/.local/bin/ag"
fi

TMP_INSTALLER=""
STATE=""
cleanup() {
    if [ -n "${TMP_INSTALLER:-}" ]; then rm -f "$TMP_INSTALLER"; fi
    if [ -n "${STATE:-}" ] && [ -d "$STATE" ]; then rm -rf "$STATE"; fi
}
trap cleanup EXIT INT TERM HUP

OPENCODE_BIN=""
if [ "$SKIP_OPENCODE" -eq 0 ]; then
    if command -v opencode >/dev/null 2>&1 && [ -x "$(command -v opencode)" ]; then
        OPENCODE_BIN="$(command -v opencode)"
        printf 'OpenCode already installed: %s\n' "$OPENCODE_BIN"
    else
        if ! command -v curl >/dev/null 2>&1; then
            err "error: curl not found; install curl or re-run with --skip-opencode"
            exit 1
        fi
        TMP_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/opencode-install.XXXXXX")"
        # Download first so a network failure is fatal before bash runs.
        if ! curl -fsSL "$OPENCODE_URL" -o "$TMP_INSTALLER"; then
            err "error: failed to download OpenCode installer from $OPENCODE_URL"
            exit 1
        fi
        # Official installer flag: never touch shell rc files.
        if ! sh "$TMP_INSTALLER" --no-modify-path; then
            err "error: OpenCode installer failed"
            exit 1
        fi
        rm -f "$TMP_INSTALLER"
        TMP_INSTALLER=""
        if command -v opencode >/dev/null 2>&1 && [ -x "$(command -v opencode)" ]; then
            OPENCODE_BIN="$(command -v opencode)"
        elif [ -n "${HOME:-}" ] && [ -x "$HOME/.opencode/bin/opencode" ]; then
            OPENCODE_BIN="$HOME/.opencode/bin/opencode"
        else
            err "error: OpenCode install reported success but no executable binary found (PATH or \$HOME/.opencode/bin/opencode)"
            exit 1
        fi
        # Binary must actually run; a doctor listing alone is not proof.
        if ! "$OPENCODE_BIN" --version >/dev/null 2>&1; then
            err "error: OpenCode binary not runnable: $OPENCODE_BIN"
            exit 1
        fi
        printf 'OpenCode installed: %s\n' "$OPENCODE_BIN"
    fi
else
    printf 'Skipping OpenCode install (--skip-opencode).\n'
fi

STATE="$(mktemp -d)"
"$AG_BIN" --dir "$STATE" selfcheck
"$AG_BIN" --dir "$STATE" agents doctor

cat <<EOF
Muse setup done.
  ag: $AG_BIN
  opencode: ${OPENCODE_BIN:-skipped}
Next (outbound SSH only, Muse dials the Mac; placeholders, Mac paths are Mac-absolute):
  ag --dir "\$HOME/.agent" hosts add mac --ssh-json '["ssh","-T","ag-mac"]' --remote-ag "/Users/MAC_USER/.local/bin/ag" --remote-state "/Users/MAC_USER/.agent" --remote-project "/Users/MAC_USER/project" --local-project "\$HOME/project"
  ag --dir "\$HOME/.agent" hosts check mac
  See docs/MULTI_HOST.md for pull/resume + cron recipe.
EOF
trap - EXIT INT TERM HUP
cleanup
