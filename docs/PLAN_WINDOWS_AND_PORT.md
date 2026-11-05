# Plan: Windows support and a faster stack for `ag`

Measured at commit `77d9f68`. The `ag` file is **19,317 lines and 1,124,040 bytes**. It grew from 19,280 lines while I was working because other merges landed, so line refs are against `77d9f68` and may drift by about 40 lines. Timings are from Linux, Python 3.13.16.


---

## 0. Summary

- **Most of `ag`'s startup time is Python compiling the 1.1 MB script on every call.** Python never caches bytecode for a script run directly without a `.py` extension. Compiling takes **0.392 s** out of the ~0.55 s total. Loading the same code from a cached `.pyc` drops `ag --help` from **~0.57 s to ~0.11 s**, about 5x faster. A tiny launcher plus an importable module gets this in under a day, with no rewrite.
- **`selfcheck` takes 9.9 s and is not hermetic.** About 4.3 s is live network fetches (models.dev and the zen privacy list). Another ~3 s is 443 `git` calls in the checkpoint selfcheck.
- **Windows:** the core JSON-state commands are portable already. The hard parts are a short list: the PTY daemon, `fork`/`setsid` daemonization, `flock`, `killpg`, FIFOs, launchd/systemd, curses, and `ps`/`lsof`.
  - Wake workers run *inside* the PTY daemon. So "headless on Windows" still needs a non-PTY daemon mode.
  - There is a dangerous trap: `os.kill(pid, 0)` is used as a "is it alive?" check in 4 places. On Windows it sends CTRL_C instead, so it is not a liveness probe.
- **Port recommendation: Go**, migrated in strangler style behind a Go front-door dispatcher. The `.agent/` file formats, lock-file paths and the JSON output format are the contract. The 23 subprocess-only test files become the black-box conformance suite.

---

## Part B first: measurements (they decide the strategy)

### B.1 Wall-clock timings

| Command | Runs (s) | Typical |
|---|---|---|
| `./ag --help` | 0.580 0.644 0.762 0.567 0.516 0.495 | ~0.57 s |
| `./ag --dir <tmp>/.agent agents list` | 0.513 0.591 0.640 0.604 0.686 0.519 | ~0.60 s |
| `python3 -c pass` | 0.017 0.016 0.019 0.017 0.018 | 0.017 s |
| `python3 -c 'import argparse,json,subprocess,shutil,shlex,difflib,pathlib,datetime,threading,hashlib,re,fnmatch'` | 0.051 0.049 0.052 0.052 0.050 | 0.050 s |
| `--help` with code loaded from the cached `__pycache__/agcpython-313.pyc` | 0.107 0.122 0.117 0.115 0.115 0.110 | **0.115 s** |
| `agents list` with code loaded from the cached `.pyc` | 0.103 0.110 0.120 0.109 0.102 0.158 | **0.110 s** |
| `./ag --dir <tmp>/sc/.agent selfcheck` | 9.896 | 9.9 s |

### B.2 Where one call's time goes (measured inside a single process)

| Phase | Time |
|---|---|
| `compile()` of the full 1,124,040-byte source | **0.392 s** |
| `marshal.loads` of the equivalent `.pyc` (1,738,569 bytes) | 0.0067 s |
| Executing the module body (defs, constants, regexes) | 0.040 s |
| `build()` argparse tree (164 `add_parser`, 647 `add_argument` calls) | 0.024 s (0.075 s under cProfile) |
| The actual `agents list` work | negligible |

So yes, **the whole 19k-line file is parsed and compiled on every call**. When `./ag` runs as a script, Python compiles it as `__main__` and writes no `.pyc`. The `__pycache__/agcpython-313.pyc` in the repo only exists because the tests load `ag` through `SourceFileLoader` under the module name `agc`.

### B.3 Import cost (`python3 -X importtime ./ag --help`, top cumulative entries in µs)

| Module | Cumulative µs | Note |
|---|---|---|
| argparse | 11,371 | needed |
| subprocess | 10,351 | needed by most commands |
| re | 8,018 | |
| concurrent.futures | 7,579 | imported at module level at line 6900 (`import concurrent.futures as _cf`); pulls in `logging` (6,440) |
| locale | 4,302 | |
| site | 4,761 | |
| pathlib | 3,390 | |
| hashlib | 3,054 | |
| shutil | 2,532 | |

Top-level imports add up to roughly 48 ms. Imports are already mostly lazy: only lines 10–12 and 6900 import at module level. For example, `curses` is imported in 21 function bodies and `fcntl` in 16.

### B.4 Selfcheck profile (cProfile, 9.3–10.2 s)

| Hot spot | Cumulative |
|---|---|
| Socket `recv_into` (live HTTP) | 3.75 s total. Of that, `modelsdev_load` → `_http_json` takes 2.93 s and `zen_privacy_load` → `_http_text` takes 1.39 s |
| `subprocess.run`, 470 calls | 3.11 s |
| `_ckpt_git`, 443 calls (from `_ckpt_selfcheck`) | 2.94 s |
| `time.sleep`, 30–88 calls | 0.79–1.07 s |
| `_setup_selfcheck` | 2.91 s |

### B.5 Ways to speed it up while staying in Python

| # | Change | Measured or estimated gain | Effort |
|---|---|---|---|
| 1 | **Launcher plus module so the bytecode is cached.** Rename the body to `agcore.py` (or `ag_impl.py`). Make `ag` a ~5-line stub that adds its own real directory to `sys.path` and runs `import agcore; sys.exit(agcore.main())`. `ag_script_path()` (line 379, uses `__file__`) must keep pointing at the launcher, because wake and `_wake_run` re-exec it (line 521). `install.sh` copies both files and runs `python3 -m py_compile`. | **0.57 s → ~0.11 s, measured** (≈ -0.45 s per call, every call: wake workers, ticks, hooks, `_acp`) | 0.5–1 day |
| 2 | Lazy argparse: build only the subparser that was invoked. Map `argv[0]` to a builder; build the full tree only for `--help` or unknown commands. | -15 to -25 ms | 1 day |
| 3 | Move the module-level `concurrent.futures` import (line 6900) into the functions that use it | -7.6 ms | minutes |
| 4 | Split into a package with lazily imported subcommand modules, e.g. `ag/core` (state, emit, locks, platform), `ag/sessions`, `ag/wake`, `ag/turn` (run_turn, ACP, streaming), `ag/relay`, `ag/tui`, `ag/setup`, `ag/catalog`. Each command loads about 10–30% of the code. | Body exec 40 ms → ~10 ms; pyc load 7 ms → ~2 ms. Most of the value is maintainability and being the prerequisite for the platform seam. | 3–5 days, mechanical; the tests' `SourceFileLoader` shims must move |
| 5 | Make selfcheck hermetic. Use the fixtures already in `tests/` (`modelsdev_fixture.json`, `or_fixture_*.json`) via env overrides instead of live HTTP. Batch or skip git checkpoint calls. Cut sleeps. | 9.9 s → ~2–3 s (estimate: -4.3 s network, -2 s git) | 1–2 days |
| 6 | Stop re-reading whole JSON/JSONL files: 12 full JSONL reads (e.g. line 11627 reads all of `events.jsonl` to keep the last 8 rows; line 2164) and 31 `json.loads(...read_text())` calls | O(file) → O(tail); matters once logs grow (logs are unbounded, per the comment at line 296) | 1 day |
| 7 | Make `save_agents` (line 5983) atomic: it writes `agents.json` directly with no temp file and replace. Also turn the 6 `Path.rename` calls into `os.replace`. | Correctness, and required for Windows | 1 hour |

**Recommendation:** ship #1, #3 and #5 right away; together they cost less than a week. They also cut how much "faster stack" matters: after #1, Python overhead is about 0.1 s per call, against agent turns that take seconds to minutes.

### B.6 Section sizes (from the `# ---` headers; line counts at `77d9f68`)

Some sections hide other subsystems: the `relay` section contains `status`/`tui`, and the `ag ui` section contains the PTY daemon.

| Subsystem (sections merged) | Lines | Python line ranges |
|---|---|---|
| Core CLI: run/read/write/edit/ls/grep/notes/todo/approvals | 294 | 1–294 |
| PTY sessions: daemon, spawn/send/snap/sessions/kill/wait/events | ~410 | 295–370, 1860–2193 |
| Wake, cohorts, reap | 1,028 | 371–1398 |
| Secret input and askpass, plus `ag ui` (macOS osascript) | ~460 | 1399–1859 |
| Harness, harness profiles, multi-backend | 951 | 2194–3144 |
| Slash commands and TUI rendering helpers | 1,680 | 3145–4824 |
| Capability/modes, mode ceiling, usage, streaming timeline | 1,177 | 4825–6001 |
| Context and auto-compaction | 899 | 6002–6900 |
| Probe, fork, agents ps, busy guard, queue, checkpoints, env | 1,487 | 6901–8387 |
| ACP, run_turn seams, retry, usage limits, rate-limit manager, api backend | 2,025 | 8388–10412 |
| Channels and schedule | 573 | 10413–10985 |
| Relay | ~670 | 10986–11657 |
| Status and TUI (`_tui_main` alone is ~1,900 lines, 55 curses refs) | ~2,055 | 11658–13713 |
| Keys and providers | 467 | 13714–14180 |
| attach/delegate/crosscheck | ~155 | 14026–14335 (partly) |
| selfcheck (`do_selfcheck` 14336–15013, plus 7 sub-selfchecks; 537 asserts in the file) | ~680 + ~400 | |
| Models cache, routing, variants, catalog, OpenRouter, privacy, access/billing, orcat, harness detection | 1,855 | 14997–16851 |
| `ag setup` front-ends (plain, web form via http.server, curses) | 1,071 | 16852–17922 |
| link (reverse SSH, lsof port scan) | 698 | 17923–18620 |
| Portable task snapshots and argparse `build()` (18431–18628) | 387 | 18621–19007 |
| Self-update and `main` | 310 | 19008–19317 |

---

## Part A: Windows support

### A.1 Inventory of POSIX-only dependencies

| Group | Count | Line refs | Windows equivalent |
|---|---|---|---|
| **pty** (`import pty`, `pty.fork`) | 1 | 1861, 1869 (`daemon_main`) | ConPTY: `CreatePseudoConsole` plus `CreateProcessW` with `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`. Possible via ctypes (about 300 lines) or pywinpty (not stdlib) |
| **os.fork / setsid double-fork daemonization** | 4 forks, 2 setsid | 456–467 (`_fork_tracked_daemon`), 18313–18321 (link) | `subprocess.Popen([sys.executable, launcher, "_daemon", sp], creationflags=DETACHED_PROCESS\|CREATE_NEW_PROCESS_GROUP\|CREATE_BREAKAWAY_FROM_JOB, close_fds=True)`. The secret env is currently passed only through fork inheritance (`_FORK_SECRET_ENV`, ~line 493); on Windows pass it through the `env=` block at process creation, never through the file. |
| `start_new_session=True` | 6 (2 already gated on `os.name`) | 690, 1917, 8497, 9046, 15428-ish, 19050-ish | `creationflags=CREATE_NEW_PROCESS_GROUP` and assign the child to a Job Object |
| **os.killpg / getpgid** | 7 / 1 | 901–909, 2105, 2118, 7989–7996 | Job Object per spawned tree (`TerminateJobObject`). Graceful stop via `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pgid)` for `CREATE_NEW_PROCESS_GROUP` children, then terminate. |
| **Signals** SIGTERM (16), SIGKILL (4), SIGINT (3), SIGWINCH (2), SIGHUP (0) | 25 | Handlers at 1044–1045, 1862, 7765, 8596(+), 18231–18232; `os.kill(pid, SIGWINCH)` at 1932; `os.kill(pid, 15/9)` at 18311–18320 | No SIGTERM delivery between processes. Use a cancel channel instead: a "stop" request file or IPC message plus a CTRL_BREAK handler (`signal.SIGBREAK`). Replace SIGWINCH with `ResizePseudoConsole`. SIGKILL becomes `TerminateProcess`/`TerminateJobObject`. |
| **`os.kill(pid, 0)` as a liveness probe (trap)** | 4 + `killpg(...,0)` 1 | 329, 2031, 7640, 7730, 7993 | On Windows `signal.CTRL_C_EVENT == 0`, so this *sends CTRL_C* instead of checking liveness. Replace with `OpenProcess(SYNCHRONIZE\|PROCESS_QUERY_LIMITED_INFORMATION)` plus `WaitForSingleObject(h, 0)` or `GetExitCodeProcess == STILL_ACTIVE`. Also record the process creation time to guard against PID reuse. |
| `os.waitpid` / WNOHANG / `waitstatus_to_exitcode` | 4 | 467, 1898, 1946 | `Popen.poll()` and the process handle |
| `os.execv*` (replace the process) | 5 | 1780, 1823, 1886 (`/bin/sh -c`), 8645, 10257 | Windows `execv` does not replace the process in place, which breaks stdio and exit codes. Spawn, wait, then `sys.exit(rc)`. |
| **termios / tty / fcntl.ioctl** | termios 15 refs, tty 1, ioctl 2 | 1499–1510 (echo off for secret input), 1651–1655, 1859/1893 (TIOCSWINSZ), 14027–14071 (`attach` raw mode) | `msvcrt.getwch` for no-echo input (or `getpass`, which works on Windows). Console raw mode via `SetConsoleMode` (clear ENABLE_LINE_INPUT/ECHO, set ENABLE_VIRTUAL_TERMINAL_INPUT). |
| `select.select` on fds and FIFOs | 2 | 1905 (pty + fifo), 14047 (stdin) | Windows `select` handles sockets only. Use threads, one blocking reader per handle feeding a queue (simplest), or overlapped I/O. |
| **`os.mkfifo`** (session stdin) | 2 | 481, 1988 | Named pipe `\\.\pipe\ag-<sid>` (via `multiprocessing.connection.Listener(family='AF_PIPE')`, which is stdlib), or TCP on 127.0.0.1 with a token written into the session dir. Writers (`do_send`, 2005) connect instead of opening the FIFO. |
| **fcntl.flock** | 40 flock refs, 16 `import fcntl` sites | 430–447 (wake job guard), 6014–6022 (context), 6531, 6738 (compact), 7623–7660 (busy guard), 7682–7810 (queue), 9606–9723 (rate-limit slots), 10683, 10706 (schedule) | `msvcrt.locking(fd, LK_NBLCK, 1)` on byte 0 (stdlib), or `LockFileEx` via ctypes for blocking/shared locks. Both are released automatically when the process dies, like flock. Today non-POSIX falls back to in-process state (7613–7657, 6736–6766), which is **not safe across processes**. |
| **os.symlink** | 3 | 16976 (skills symlink into `~/.claude`), 8698–8699 (ACP selfcheck) | Directory junction (`_winapi.CreateJunction`, undocumented but present in CPython, or `cmd /c mklink /J`). Fall back to copying. Real symlinks need Developer Mode or admin. |
| `/proc` | 0 | none | no change |
| **`ps` / `lsof`** | `ps` 5 call sites, `lsof` 1 | 897 (reap identity check), 1681 (askpass parent), 7101, 7113 (`agents ps` tree), 16520 (harness detection), 17967 (link ports) | `CreateToolhelp32Snapshot` (pid/ppid/exe) via ctypes; command line via WMI/PowerShell `Get-CimInstance Win32_Process` (slow) or `NtQueryInformationProcess`; ports via `GetExtendedTcpTable` or `netstat -ano` |
| **Shell assumptions** `shell=True` (4), `sh -c` (2), `/bin/sh` (2) | 8 | 123 (`ag run`), 567 (wake `cmd` check), 690 and 1917 (`on_exit`), 1886 (daemon exec), 12013, 1616 (askpass stub `#!/bin/sh`) | Pick the shell per platform: `cmd.exe /d /s /c` (via `COMSPEC`) or `pwsh -NoProfile -Command`, with an `AG_SHELL` override. Document that spawn command strings are shell-specific. |
| `shlex` | 20 | e.g. 10844, 18653, 1714 | `shlex.split`/`quote` are POSIX-only. Use `subprocess.list2cmdline` on Windows; keep `shlex.join` only for strings that go to a remote POSIX host over ssh (that part is correct as-is). |
| `shutil.which` | 11 | | Works, honoring PATHEXT. Important: npm-installed backends are `claude.cmd`/`gemini.cmd` shims, and `Popen(["claude", ...])` without `which` will not find them. Always resolve through `which`. Running `.cmd` files also goes through cmd.exe argument parsing (the "BatBadBut" problem), so prefer each backend's native `.exe` or `node <script>`. |
| **launchd / systemd / cron** | launchd 7 refs, systemd 18 refs | 10603–10985: `_sched_plist`, `launchctl bootstrap gui/{uid}` (10844, which uses `os.getuid`), `_sched_systemd_units` (~10798), `--cron` (prints a line) | Task Scheduler: `schtasks /Create /SC MINUTE /MO <n> /TN "ag-tick" /TR "<launcher> schedule tick --all --reap"`, or an XML task via `schtasks /Create /XML` for no-window and battery settings. Launch through `pythonw.exe` or a `.vbs`/hidden mode to avoid console flashes. |
| **Askpass** (SUDO_ASKPASS, SSH_ASKPASS) | 8 | 1612–1636 (writes `#!/bin/sh` stubs, `chmod 0o700`), 1661 | No sudo on Windows (drop it). OpenSSH for Windows supports `SSH_ASKPASS` with `SSH_ASKPASS_REQUIRE=force`, so the stub becomes `askpass.cmd` (`@"<python>" "<launcher>" askpass %*`). `GIT_ASKPASS` works the same way. |
| **Secret/notification UI** (osascript) | 5 | 1450, 1714, 1760; `ui_enabled()` 1737 is darwin-only | Phase 3: PowerShell `Get-Credential`/`Read-Host -AsSecureString`, or a Tk dialog (tkinter is usually present on Windows Python). Toasts via PowerShell BurntToast or the WinRT API. `AG_UI_CMD`/`AG_ASKPASS_CMD` already allow pluggable programs. |
| Keychain (`security` CLI) | macOS-gated (13745) | 13714–14180 | Windows Credential Manager via ctypes `advapi32.CredReadW/CredWriteW/CredDeleteW` (stdlib-only), target name `ag:<NAME>` |
| **ssh** | relay/hosts (18653 `host["ssh"] + shlex.join(remote)`), link 17979, `ssh-keygen` 18000 | | Windows ships OpenSSH client (`C:\Windows\System32\OpenSSH\ssh.exe`). No ControlMaster multiplexing on Windows, so expect slower relay. The remote side stays POSIX. `shlex.join` for the remote command is correct. |
| **Paths** | `/tmp` 8 (mostly selfcheck fixtures), `expanduser`/`~` 66, `os.pathsep` 6 (correct), literal `":"` split 3 (5685, 7058, 10628 are not PATH-related; fine) | `_ag_home`/`ag_config_home`/`ag_cache_home` at 15050–15052 use `~/.config/ag` and `~/.cache/ag` | Use `%APPDATA%\ag` and `%LOCALAPPDATA%\ag` by default on Windows, keeping `AG_HOME`/`AG_CONFIG_HOME`/`AG_CACHE_HOME` overrides. `/tmp` literals become `tempfile.gettempdir()`. |
| **Text encoding** | 99 `read_text()`, 57 `write_text()` without encoding, 47 `open(`, only 1 `encoding=` | everywhere | Windows defaults to cp1252 before Python 3.15. Either set `PYTHONUTF8=1` in the launcher (`ag.cmd`) or add `encoding="utf-8"` through one helper. JSON state must be UTF-8 bytes on every OS. |
| **Atomic rename / unlink** | `Path.rename` 6 (316, 416, 781, 2821, 6700, 7806), `unlink` 29, `O_EXCL` claims 6 | | `Path.rename` raises if the target exists on Windows, so switch to `os.replace`. Replace/unlink fail with a sharing violation while another process has the file open; add retry with backoff in `_swrite`. `O_EXCL` create works. |
| Append-mode JSONL from many processes | `_event` 304 and others | | Appends are not guaranteed atomic on Windows. Write each line with one `os.write` on an `O_APPEND` fd, or take the per-file lock. |
| Executable bits / chmod | 6 | 1613, 1621 (`0o700`), 4996 (`os.X_OK`), 19103 | chmod is a no-op on Windows; file ACLs give per-user secrecy (the default under `%LOCALAPPDATA%` is user-only). `X_OK` must become a PATHEXT check. |
| **curses** (TUI, setup CLI form) | 132 refs, 21 imports | `_tui_main` 11782 (~1,900 lines), `curses.wrapper` 11673, 17707 | Not in the Windows stdlib. Options: the `windows-curses` wheel (breaks stdlib-only), or drop the TUI on native Windows in phases 1–2 and point users to the web form (setup already has a "web" UI built on stdlib http.server at 17357–17415, and it works on Windows). |
| Launch: shebang, no extension | `#!/usr/bin/env python3` | line 1 | `ag.cmd` shim: `@echo off & set PYTHONUTF8=1 & py -3 "%~dp0agcore.py" %*`, plus `ag.ps1`. Or a pip/pipx console script, which generates `ag.exe`. A Go port makes this go away. |

Existing Windows awareness: there are **29** `os.name`/`sys.platform` checks. Examples: `do_spawn` refuses on non-POSIX (1961), and lock helpers return `None` or fall back to in-process state (6013, 6530, 7657, 7680, 7783, 7987).

### A.2 How the backends behave on Windows

| Backend | Native Windows | Notes for `ag` |
|---|---|---|
| claude (Claude Code) | Yes: native installer and npm | Ships as `claude.exe` (native) or `claude.cmd` (npm). Its Bash tool wants Git for Windows (Git Bash) or falls back to PowerShell. Headless `-p --output-format stream-json` behaves the same. `CLAUDE_CONFIG_DIR` handling (8262–8387) is portable. |
| codex | Yes (npm; native Windows sandbox is newer, and WSL is still recommended by upstream) | `-s danger-full-access` and the other sandbox modes map differently on Windows. Treat the read-only/sandbox modes as "verify per release". `CODEX_HOME` is portable. |
| opencode | Yes (Windows builds via npm and release zip) | Binary lookup at 4990–4992 is hard-coded to `~/.opencode/bin`, `~/.local/bin`, `~/.bun/bin`. Add `%USERPROFILE%\.opencode\bin\opencode.exe` and PATHEXT lookup. |
| gemini | Yes (Node, npm `.cmd` shim) | Mind `.cmd` quoting |
| cursor (`agent`) | Verify. Historically macOS/Linux, plus WSL | Path at 4981 is `~/.local/bin/agent` |
| ACP agents (stdio JSON-RPC) | Portable (pipes) | ACP transport (8388–8713) uses `Popen` pipes. `start_new_session` is already gated (8497). Needs threaded readers if it uses `select` on pipes. |
| api backend (HTTP) | Portable | urllib |

**WSL fallback:** inside WSL2 everything works unchanged, since it is Linux. The cost is that agents in WSL work on `\\wsl$` or `/mnt/c` paths, and `/mnt/c` I/O is slow. Recommend a repo clone inside the WSL filesystem.

### A.3 Platform seam design

Put every OS-specific call in one module, `agplat.py`, or one `# --- platform ---` section in the single file. Ban direct use of `fcntl`, `pty`, `termios`, `os.killpg`, `os.fork` and `os.mkfifo` everywhere else, and enforce the ban with a grep test in `tests/test_ag_doc_coverage.py` style.

```python
# agplat: one seam, two implementations (posix / nt), chosen at import.
class FileLock:                       # context manager; crash-released
    def __init__(self, path, exclusive=True, blocking=True, timeout=None): ...
def lock(path, *, blocking=True, timeout=None) -> FileLock     # flock | msvcrt.locking/LockFileEx

class ProcGroup:                      # returned by spawn_group
    pid: int; create_time: float      # identity survives pid reuse
    def alive(self) -> bool           # kill(pid,0) | OpenProcess+GetExitCodeProcess
    def terminate(self, grace_s=5)    # killpg TERM→KILL | CTRL_BREAK → TerminateJobObject
def spawn_group(argv, *, cwd, env, stdin=None, stdout=None, detached=False) -> ProcGroup
                                      # start_new_session | CREATE_NEW_PROCESS_GROUP + Job Object
def daemonize(argv, *, env) -> int    # double-fork | Popen(DETACHED_PROCESS|BREAKAWAY)
def kill_group(pid_or_group, force=False)
def proc_alive(pid, create_time=None) -> bool
def proc_table() -> list[dict]        # ps -axo | Toolhelp32 (+ cmdline when cheap)

class Pty:                            # read(n), write(b), resize(rows, cols), wait(), pid
def pty_open(argv_or_shell, *, cwd, env, rows, cols) -> Pty   # pty.fork | ConPTY (ctypes)

class Channel:                        # session stdin / control
def ipc_listen(session_dir) -> Listener  # mkfifo | \\.\pipe\ag-<sid> (AF_PIPE) w/ token file
def ipc_connect(session_dir) -> Channel

def shell_argv(cmd_str) -> list[str]  # ["/bin/sh","-c",s] | [COMSPEC,"/d","/s","/c",s] | AG_SHELL
def quote_cmdline(argv) -> str        # shlex.join | subprocess.list2cmdline
def link_dir(target, link)            # symlink | junction | copy
def replace_atomic(tmp, dst)          # os.replace + Windows sharing-violation retry
def config_home()/cache_home()/runtime_dir()
def raw_terminal() / no_echo_input()  # termios/tty | SetConsoleMode/msvcrt

class Scheduler:                      # install(interval, argv) / uninstall() / status()
    # LaunchdScheduler | SystemdUserScheduler | CronPrinter | SchtasksScheduler
def scheduler_backend(pref=None) -> Scheduler
def secret_store() -> SecretStore     # macOS `security` | Windows CredRead/CredWrite | (Linux: secret-tool, future)
```

Rules:
1. Locks are part of the on-disk contract: same file paths, one byte range. Keep it that way so Python and a future Go binary interoperate.
2. The daemon gets a **pipe mode** (no PTY) as well as PTY mode. Wake workers (`_spawn_tracked` 496 → `_wake_run`) only need pipe mode.
3. All cancellation becomes a "request file plus wake" protocol (the queue already claims tickets by unlink at ~7730). Signals stay an optimization on POSIX.

### A.4 Phased path

| Phase | Scope | Work items | Est. |
|---|---|---|---|
| **0: WSL officially supported, plus docs** | README/AGENTS.md: "POSIX; Windows via WSL2". `docs/INSTALL.md` WSL section: install backends *inside* WSL, keep the repo on ext4, use `wslview`/`explorer.exe` for links. Make the `ag setup` web form open the browser via `wslview` when `WSL_DISTRO_NAME` is set (17431 uses `open`/`xdg-open`). | docs; a 5-line WSL detection | 1–2 d |
| **1: core headless on native Windows (no PTY)** | Commands: run/read/write/edit/ls/grep/notes/todo, agents/roles/receipts/models/route/keys-less providers, `chat`/`delegate`/`crosscheck`/`queue`/`stop`/`wake`/`wakes --reap`/channels/context/compact/checkpoints, `_acp`, `ag setup --ui web`. | Introduce `agplat` with lock / spawn_group / kill_group / proc_alive / daemonize / replace_atomic / homes. UTF-8 everywhere (`PYTHONUTF8=1` in the shim, then explicit encodings). Pipe-mode daemon for tracked workers: wake runs through it, stdin channel over a named pipe. Remove the in-process lock fallbacks. Resolve backend exes via `which` (PATHEXT). `ag.cmd`/`ag.ps1` shim; `install.ps1`. `agents ps` via Toolhelp32. Shell = cmd/pwsh for `run`/`on_exit`. | 3–4 wk |
| **2: PTY sessions via ConPTY** | spawn/send/snap/attach/kill/wait/sessions; resize | ctypes ConPTY (CreatePseudoConsole, ResizePseudoConsole, pipes + reader threads). `attach` raw console. SecretWatch/SecretScrub on ConPTY output; ConPTY emits VT sequences that the existing `ANSI` regex at line 300 must tolerate (it repaints more often). Job Object kill. TUI: either the optional `windows-curses` dependency or "TUI not on native Windows". | 3–4 wk |
| **3: schedules, secrets, relay, link** | `schedule install` → schtasks. Keys → Credential Manager. askpass `.cmd` stub with Windows OpenSSH `SSH_ASKPASS`. Relay over Windows OpenSSH (no ControlMaster). `link` (reverse ssh, ports via `GetExtendedTcpTable`). `ag ui` via PowerShell/Tk. Self-update (git) works as-is. | 3–4 wk |

### A.5 CI matrix (new; the repo has no `.github/workflows` today)

```yaml
# .github/workflows/test.yml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python: ["3.9", "3.13"]          # README promises >=3.9 (os.waitstatus_to_exitcode)
    exclude: [{os: windows-latest, python: "3.9"}]
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with: {python-version: "${{ matrix.python }}"}
  - run: git config --global user.email ci@x && git config --global user.name ci
  - name: selfcheck
    run: python ag --dir ${{ runner.temp }}/sc/.agent selfcheck
    env: {PYTHONUTF8: "1", AG_OFFLINE: "1"}          # after selfcheck is hermetic (B.5 #5)
  - name: tests
    shell: bash
    run: for t in tests/test_*.py; do python "$t" || exit 1; done
```

- On Windows, start with an **allowlist** of phase-1 test files (`tests/windows_allow.txt`) and grow it each phase.
- Add a `wsl` job later via `Vampire/setup-wsl` for the phase 0 claim.
- Add a lint job that greps for forbidden POSIX calls outside `agplat`.

---

## Part B (continued): port to a faster stack

### B.7 Is a rewrite justified for speed?

After the launcher fix, `ag` costs **~0.11 s** per call. Python's floor is 0.017 s, and imports plus argparse add about 0.07 s. A Go or Rust binary starts in about 2–10 ms. The remaining ~0.1 s matters for:
- high-frequency callers: hooks, `snap`/`events` polling, schedule ticks, `ag` invoked many times inside a turn;
- Windows, where Python process creation is slower and the interpreter must be installed;
- distribution: a single binary and no Python requirement on Windows.

It does not matter much for agent turns, which take seconds to minutes. So the port's real value is **distribution and Windows**, not raw speed.

### B.8 Target comparison

| | Go | Rust | TypeScript/Bun |
|---|---|---|---|
| Startup | ~2–5 ms | ~1–3 ms | ~20–40 ms (bun-compiled) |
| Single binary / cross-compile | `GOOS=windows go build`, static, ~10–20 MB; trivial cross-compile from one Linux runner | Static, ~5–15 MB; cross-compiling to Windows needs `cargo-xwin` or a Windows runner | `bun build --compile`, ~50–100 MB binary; cross-target flags exist |
| PTY | `creack/pty` (POSIX); ConPTY via `UserExistsError/conpty` or `aymanbagabas/go-pty` (one API over both) | `portable-pty` (wezterm), mature on both | `node-pty` is a native addon; compatibility with Bun and `bun --compile` is shaky. Biggest risk. |
| Process groups / jobs / locks | `golang.org/x/sys/windows` (Job Objects, LockFileEx), `syscall.Flock` | `windows-sys`, `fs4`/`fd-lock` | Node has no flock or job objects; needs native addons or `proper-lockfile` (mkdir-based, not crash-released) |
| Concurrency (daemon, watchdogs, rate-limit slots, ACP) | goroutines and channels fit the daemon/reader-thread model well | async (tokio) or threads; more ceremony | event loop is natural for ACP and streaming |
| TUI | bubbletea/tcell (Windows console supported) | ratatui/crossterm (Windows supported) | ink/blessed |
| Sharing code with T3 Code (Node/TS + Effect) | None; port ACP ~330 lines | None | Could share ACP and provider-policy code. But `ag`'s ACP is already a port of T3 Code's, and T3 Code is built on Effect, so reuse is partial anyway. |
| Ramp-up and porting speed from dense Python | Fast; expect ~2.5–3x LOC (≈45–55k lines) | Slowest; ~2.5x LOC, more design time | Fastest to write; ~1.5–2x LOC |
| JSON/argparse parity | encoding/json (field order: use `json.RawMessage` or ordered maps where tests compare text); cobra/kong for CLI | serde + clap, excellent | native JSON |

**Recommendation: Go.**
- Its process, PTY, locking and Job Object libraries cover every row of A.1 on all three OSes.
- It compiles three single binaries from one CI runner, and startup is effectively zero.
- The goroutine model maps directly onto the existing daemon/watchdog/slot code.
- Velocity is acceptable for a ~19k-line dense Python codebase.

Rust is the choice only if peak performance or memory safety in a long-running daemon becomes a requirement. Bun is the choice only if sharing code live with T3 Code is a hard goal. Even then, PTY on Windows under `bun --compile` is the deal-breaker to test first with a spike.

### B.9 Strangler migration plan

**Contract** (frozen and documented in `docs/specs/CONTRACT.md`):
1. On-disk state under `.agent/` and the global homes: `agents.json`, `roles.json`, `events.jsonl`, `sessions/<sid>/{status.json,output.log,stdin.fifo|pipe,winsize.json}`, the `wake/` jobs and groups, queue tickets, schedules, `$AG_CONFIG_HOME/*.json`, `$AG_CACHE_HOME/ratelimit/slots/<key>/<i>.lock`.
2. **Lock-file paths and semantics.** Both implementations must take the same flock/LockFileEx on the same files while they coexist.
3. CLI surface and exit codes; the JSON envelope `{"ok":bool,"cmd":str,"data":{...},"error"?,"hint"?}` (from `emit`, line 64); `--json` and `--dir` lifting rules (`main` at 19273, `_lift_trailing_dir`).

**Dispatcher:** the Go binary `ag` becomes the front door.
- It parses the global flags, then checks whether the subcommand is ported.
- If ported, it runs it natively. If not, it runs `python3 agcore.py <same argv>` with inherited stdio and passes the exit code through.
  - POSIX: use `syscall.Exec`.
  - Windows: spawn and wait, inside a Job Object.
- Set `AG_IMPL=py|go` to force one side for A/B testing.
- Python's `ag_script_path()` must return the Go launcher so that workers Python spawns re-enter through the dispatcher.

**Order (leaf commands first):**
1. Platform seam (`internal/plat`): locks, procs, homes, atomic replace, JSON envelope, `--dir`/`--json` handling. Port `run/read/write/edit/ls/grep/note(s)/todo/history/approvals/approve`, `events`, `sessions`, `agents list/add/rm`, `roles`, `receipts`.
2. Read-only catalog commands: `models`, `route`, `ratelimit` (status), `usage`, `budget`, `access`, `providers list`.
3. Queue, busy guard and wake: `wake`, `wakes`, `queue`, `stop`, cohorts, reap. This is the locking-critical step; run the Python and Go implementations concurrently in tests.
4. Turn engine: `run_turn` seams, streaming parsers for each backend, ACP, retry, limits, rate-limit slots, api backend, context, compaction, checkpoints, `chat`/`delegate`/`crosscheck`/`channel`/`handoff`.
5. PTY daemon and `spawn/send/snap/kill/wait/attach` with creack/pty and ConPTY. This lands Windows phase 2 almost for free.
6. `schedule` (launchd/systemd/schtasks), `keys` (keychain/CredMan), `relay`, `hosts`, `sync`, `link`, snapshots, `update`.
7. TUI and setup UIs last. Rebuild them in bubbletea, or keep the web form served by Go's net/http.

**Tests as conformance:**
- **23 test files use only subprocess** (no module import), so they are black-box today: agents_sessions, api_compat, channel, chatlog, cli, devmode, handoff, parallel, profiles, queue, retry, route_effort, setup, spawn_secret_env, ssh_wake, wake, wake_control, wake_validate, wake_wait, web, worktree, install, setup_muse.
  - They hard-code `AG = str(Path(__file__).resolve().parent.parent / "ag")` (24 occurrences). Change that to `os.environ.get("AG_BIN", ...)` and run the same suite against both implementations in CI.
- **46 files import the module** (`SourceFileLoader`/`module_from_spec`); 7 of them do only that. These are white-box. Keep them for the Python side, and port the pure functions they cover (parsers such as `_parse_opencode`, `crosscheck_verdict`, `opencode_permission`, `mode_flags`, `env_apply`) to Go table-driven tests, using the same fixtures (`tests/*fixture*.json`).
- The embedded `selfcheck` (do_selfcheck at ~678 lines plus 7 sub-selfchecks; 537 `assert`s in the file) is white-box too. Split it into the black-box pieces (CLI round-trips) and unit tests.
- Add a "golden JSON" harness: run each ported command under both implementations against the same state dir and diff the outputs, ignoring `ts` fields.

### B.10 Size and effort estimates for a Go port

LOC uses ~2.5–3x the dense Python, which uses one-liners and semicolons heavily. Effort is for one experienced engineer and includes conformance tests.

| Subsystem | Py lines | Est. Go lines | Effort (wk) | Notes |
|---|---|---|---|---|
| Core CLI, envelope, dispatcher, platform seam | 294 + seam | 2,000 | 2 | Includes Windows locks/procs |
| PTY daemon and sessions (POSIX and ConPTY) | ~410 | 1,500 | 2–3 | |
| Wake, cohorts, reap | 1,028 | 2,800 | 2–3 | Locking-critical |
| Secret input/askpass/ui | ~460 | 1,200 | 1–2 | Per-OS UIs |
| Harness and profiles, multi-backend | 951 | 2,500 | 2 | |
| Slash commands and TUI helpers | 1,680 | 4,000 | 2–3 | |
| Modes/ceiling/usage/streaming | 1,177 | 3,200 | 2–3 | Per-backend stream parsers |
| Context and compaction | 899 | 2,300 | 1.5 | |
| Probe/fork/ps/busy/queue/checkpoints/env | 1,487 | 3,800 | 2–3 | |
| ACP, run_turn, retry, limits, rate-limit, api | 2,025 | 5,500 | 4–5 | Core engine, highest risk |
| Channels and schedule | 573 | 1,500 | 1–1.5 | Plus schtasks |
| Relay | ~670 | 1,800 | 1.5–2 | |
| Status and TUI (`_tui_main`) | ~2,055 | 4,500 | 3–4 | Or a reduced redesign |
| Keys and providers | 467 | 1,200 | 1 | Plus CredMan |
| Crosscheck/delegate/attach | ~310 | 800 | 0.5–1 | |
| Catalog/routing/variants/OpenRouter/privacy/access | 1,855 | 4,500 | 3 | Mostly pure data, easy |
| Setup front-ends | 1,071 | 2,500 | 2 | Web form plus prompts |
| Link and snapshots | ~1,085 | 2,600 | 2 | |
| Parser and update/main | ~510 | 1,200 | 1 | |
| Selfcheck → unit tests | ~1,080 | (tests) | 2 | |
| **Total** | **~19,300** | **~50,000** | **~38–48 wk** (≈9–11 engineer-months) | Parallelizable after the seam and engine |

For comparison: the Python streamlining (B.5 #1–#7) is about **1.5–2 weeks** for most of the speed gain. Windows phases 0–3 in Python are about **10–12 weeks**.

### B.11 Recommended sequencing

1. **Week 1:** launcher plus pyc caching, lazy `concurrent.futures`, hermetic selfcheck, atomic `save_agents`/`os.replace`. Add the CI matrix (Linux/macOS).
2. **Weeks 2–3:** phase 0 (WSL docs). Extract the `agplat` seam in Python, which is also the spec for Go's `internal/plat`. Freeze the contract doc. Add `AG_BIN` to the 23 black-box tests.
3. **Then decide:**
   - Go port, if a single binary and native Windows are product goals. Phases 1–3 for Windows then happen in Go, with no Python Windows work beyond the seam.
   - Otherwise, Windows phases 1–3 in Python on top of `agplat`.

   The seam work is useful under both choices, so it should come first either way.

### Critical Files for Implementation
- /home/user/ag/ag (key areas: `daemon_main` 1860, `_fork_tracked_daemon` 455, lock helpers 430/6014/7608–7810/9606, schedule 10603–10985, `main` 19273, `build` 18431, `ag_script_path` 379, homes 15050)
- /home/user/ag/install.sh
- /home/user/ag/tests/test_ag_cli.py (the black-box test pattern; `AG = ...` path constant to parameterize)
- /home/user/ag/README.md and /home/user/ag/docs/INSTALL.md (POSIX-only claims at README:3, :59, :598; AGENTS.md:3)
- /home/user/ag/AGENTS.md
