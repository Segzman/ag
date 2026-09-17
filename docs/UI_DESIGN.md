# docs/UI_DESIGN.md — ag TUI redesign (UI owner persistent brief)

## Sources

- Rejected predecessor: boxed roster UI ("primitive").
- Visual references (inspected with image tool):
  - `/private/tmp/ag-redesign-references/opencode-home.png` — home:
    pixel wordmark, `Ask anything… "…"` composer, `Build GLM-4.7
    OpenCode Zen` second line, `tab switch agent  ctrl+p commands`,
    quiet `~/…` cwd bottom-left + version bottom-right.
  - `/private/tmp/ag-redesign-references/opencode-official.webp` —
    session: `# title + tokens` header, `Find the …` filled user
    bubble, plain agent text with `* Grep` tool lines, bottom filled
    composer (`Build Claude Opus 4.5 OpenCode Zen`), `esc interrupt …
    ctrl+t variants  tab agents  ctrl+p commands` footer.
- Primary refs: https://opencode.ai/docs/tui/ and
  https://opencode.ai/docs/ ; home shot via
  https://codestandup.com/posts/2026/opencode-tutorial/ .

## Design decisions

- Centered small ASCII `ag` wordmark (`ag_wordmark_lines`,
  full/small/tiny/none by `wordmark_size(h,w)`); generous negative
  space; content `min(84, w-4)` centered (`content_region`).
- Composer-first, no insert mode: home boots in `homeinput`, chat in
  `input`; every ordinary letter is text (`q/i/j/k/m/P` never actions
  while composing). `Esc` moves to selection (arrows/tab/1-9, `q`
  quits, any text returns to the composer); chat `Esc` goes home with
  the draft kept. Placeholders/hints teach `Type a message or /
  for commands`, `Enter send`, `Esc select/back` — never `i`.
- Charcoal filled composer + thin blue LEFT rule (`▍`) on home and
  session (`draw_composer`); off-white text, dim secondary labels,
  friendly model (`friendly_model`: ag OpenCode default →
  `Muse Spark 1.3`, `provider/x` → `x`); full ids only in pickers.
- Slash popup (`SLASH_COMMANDS` single table driving popup + `/help`):
  leading `/` opens a compact searchable list anchored to the composer
  (`slash_popup_rect`: above it, or below it when the composer sits at
  the top of a tiny screen; status line never covered). Letters filter
  in place (`slash_popup_filter`: prefix before description match),
  `↑↓` move, `Tab` completes canonical text via `slash_popup_complete`
  (args kept, never executes), `Enter` runs (`run_slash_entry`: UI
  route opens the screen, slash route runs the TYPED line verbatim so
  `/model opus`, `/profile use x`, `/harness use b` are never
  shadowed; the executed line is consumed from the originating composer
  BEFORE navigation, so `Esc` back never resurrects `/config` under new
  typing), `Esc` dismisses keeping the draft (suppressed until `/`
  is retyped), Backspace past `/` dismisses, empty match is honest
  (`(no match — Enter runs typed text)`, unknown `/word` errors via
  `handle_slash`, never model prose). `/` mid-sentence stays literal
  (`parse_slash` only fires on a leading slash; `// comment` is prose;
  command words allow letters/digits/`-`/`_` so `/compact-now` parses;
  a `/` glued to a KNOWN word (`/config/quit`) parses as a malformed
  command and errors locally, while unknown-first-word paths
  (`/Users/me/file`) stay prose). `SLASH_KNOWN` covers every
  `SLASH_COMMANDS` entry (backslash alias included).
- Canonical popup entries: `/projects /agents /config /model /backend
  /profile /new /delegate /context /compact /compact-now /theme
  /handoff /approve /harness /help /quit`. `/projects`/`/agents` open
  overlay choosers (`projpick`/`agentpick`: arrows + Enter + Esc,
  click parity, `pk_return` back-stack, drafts kept). Bare `/model`,
  `/backend`, `/profile` open their pickers; with args they run the
  typed form. `/new`, `/delegate`, `/model` custom strings capture
  into `flow_inp`, never into the message draft. `/quit` exits,
  `/compact-now` reports the honest `start_compact` outcome (refused-busy
  shows the refusal, never a fake started message), `/theme` cycles,
  `/approve` approves oldest. Headless `/projects`-style UI-only
  commands answer honestly (`SLASH_UI_ONLY` branch: open `ag tui`).
- Cursor honesty (`_cursor`): visible at the composer/edit focus
  (`homeinput`/`input`/capture modes, cell-measured insertion point),
  hidden in navigation and dialogs. `Ctrl+C` exits 130 via
  `curses.wrapper` cleanup (no traceback); other errors propagate.
- Popup scroll (`slash_visible_window`): `↑↓`/PgUp/PgDn move the
  selection and the window follows, so the selected row is always
  visible; mouse hit mapping (`pos = slash_off + (my - by - 1)`) reads
  the same offset — keyboard and click agree.
- Quiet bottom `cwd · status` line; busy shows honest `working Ns`
  near the composer (elapsed only, never fake progress); no
  count-header duplication, no fake cost/token data anywhere.
- Real 256-color pairs (`theme_pairs`, pairs 10–22) with 8-color
  fallback and attr-0 headless fallback; themes preserved
  (abyss/mono/ember). Text drawn on charcoal uses bg-carrying pairs
  (`text_c`, `dim_c`, `text_u`, `alert_c`) so surfaces stay continuous.
  New `diffadd`/`diffdel` roles (green/red, pairs 21–22) for unified
  diffs; `NO_COLOR` returns zero attrs (selection falls back to
  reverse video via `select_attr`, meaning rides on `>`/`!`/`+-`/
  status words); opt-in `AG_ASCII` swaps glyphs via `G()` (default
  Unicode untouched).
- Cell-aware widths (`cell_len`/`cell_trunc`/`cell_wrap_line` over
  grapheme-glued clusters: CJK wide = 2, combining/ZWJ/skin-tone = 0,
  never split, never dropped) in ALL message rows: `wrap_chat_line` is
  cell-based, so popup, choosers, timeline cards, diff truncation,
  composer/hint/status lines and user/agent/tool prose all measure
  terminal cells, never Python chars.
- Session: subtle header, roomy transcript, charcoal user bubble
  (`userfill` + `text_u` on abyss 235, blue `▍` left rule like the
  composer), filled composer, optional wide sidebar (agents +
  approvals, only ≥110 cols); single pane below that;
  no box cages on main surfaces. Shared `draw_panel` charcoal dialog
  for pickers/palette/config/compact/context/choosers/popup.
- `PAIR_ROLES` maps pair number → role so headless QA can repaint the
  exact theme RGB (`theme_rgb`) from captured attrs.
- Mouse is best-effort augmentation (click rows/popup/composer,
  wheel = transcript scroll, all `try/except` around
  `mousemask`/`getmouse`); every action keeps keyboard parity, never
  a mouse-only path; no fake button chrome.

## Live timeline display (UI side of the shared contract)

Backend engine (stream-v2 owned: `parse_timeline_*`, `TimelineBuffer`,
`run_turn` emission, chat-log persistence, `tests/test_ag_streaming.py`,
`docs/STREAMING.md`) emits `on_event(name, "timeline", row)` with
`{event_id, role, text, tool_name?, tool_id?, status?, path?,
timeline_summary?}`. The TUI owns display only:

- `timeline_upsert`/`timeline_rows` (pure): replace-by-`event_id`,
  first-seen order — re-emitted progress never concatenates.
- `load_chat_view` (pure of backend I/O beyond the log read): dedups by
  `event_id` (last-wins, legacy rows without `event_id` preserved)
  BEFORE slicing the last-60 display window, so snapshot rewrites never
  push real turns out of view. Used for init, exit reload, and compact
  reload alike.
- `onevent` timeline branch: upserts into a per-agent live store;
  no status is ever inferred — a tool row without status shows no
  status word, live or persisted; only an explicit backend status
  renders. Legacy `text`
  callbacks still feed `stream_text` (suppressed while timeline
  `assistant` rows exist, no double render); legacy `tool`/`toolout`
  lines append to the in-memory transcript ONLY when the live store has
  no tool-family rows yet — one invocation never shows as both a card
  and a legacy line. `error` rows also set the status line so they
  survive partial responses. `exit` reloads via `load_chat_view`
  (deduped rows match live rows by contract) and drops the live store.
- `chat_screen_lines` renders timeline roles chronologically
  interleaved with legacy rows: `assistant` shares the agent header
  (one header per contiguous block split by tool use, never per
  chunk); `reasoning` is a subdued `▸ Thinking` section;
  `tool` rows with fields render as `◆ name args` cards plus the
  status word only when the backend sent one (`timeline_tool_summary`:
  Running/Done/Failed mapping, path first, engine-composed `Name
  args` text reused not doubled, JSON blobs dropped); `diff` rows
  render via `diff_screen_lines` (whitespace + `+-` kept, truncated
  never wrapped, `─ path` header); `error` rows render as `! …`
  alert lines; per-row bodies over `omit_cap` (default 60) end with an
  explicit `… (N more lines · full text in chat log)` marker; a final
  aggregate `agent` row with `timeline_summary=true` is suppressed
  only when assistant rows from the SAME turn (after the nearest
  preceding user row) already carry the answer — a legacy aggregate
  from another turn still shows.
- No optimistic user echo: the user row appears when the backend
  emits it (or on turn-end reload for legacy backends) — never
  duplicated. Refused submits (agent busy) keep the unsent draft with a
  visible error instead of clearing it. No invented thinking/progress/diffs:
  only emitted rows render. Scroll follow is offset-based: new data follows the bottom
  only when already there; reading history never yanks.
- Known backend limitations (read from the engine, not re-verified):
  diffs come only from known edit inputs (`tl_diff_for_tool`, never a
  worktree scan; input-derived diffs are labeled `proposal
  (unconfirmed)`); `echo` backends emit one assistant row per line
  (rendered like any timeline assistant).

## User-flow contract

- Home (initial, composing `homeinput`): type immediately (`q` is
  text); `Enter` submits the draft to the selected agent and opens
  chat, `Enter` on empty draft moves to selection, `/` at the start
  opens the slash popup, `Esc` keeps the draft and selects.
  Selection (`normal`): `↑↓/jk/tab/1-9` select (identity-mapped via
  `index_by_name`, flat order ≠ roster order; agent window with
  per-group headers + front-trim keeps the row visible), `Enter`
  opens chat, `q` quits, unbound text returns to the composer,
  `c/m/M/b/P/R/n/d/a/t/?` keep their picker/flow actions,
  `Ctrl+P` opens the searchable palette (optional alias, drafts kept).
- Chat (composing `input`): type immediately, `Enter` sends (`!cmd`
  runs shell, `/cmd` runs locally), `↑↓/PgUp/PgDn` scroll, `Esc` back
  home with the draft kept, `Ctrl+P` palette, mouse wheel scrolls.
  Selection keys reachable after `Esc`→home; no letters hijacked while
  composing.
- `Ctrl+P` (16) global, incl. while typing: searchable palette
  (`palette_entries` pure table, `palette_filter`), arrows/Enter/Esc,
  real actions (home/chat/config/model/backend/profile/handoff/new/
  delegate/theme/approve/help/quit/compact/context). Cancel restores
  `(screen, mode)`; drafts (`inp`/`home_inp`/`flow_inp`) never cleared.
- Configurator: selectable fields (`config_fields` + arrows + Enter,
  plain labels, per-row help, explicit Back row), shared dialog,
  wrap/scroll, guarded existing ops; `compact` shows the effective
  On/Off and opens the menus below; `context` opens the read-only
  view; pickers return via `pk_return` (back to the composer that
  opened them, or to the configurator for field-pickers, same row);
  Esc follows the back-stack with drafts intact. Footers shrink by
  measured cells at narrow widths (`foot_text`: full wording, else
  `↑↓ choose · Enter · Esc back`) so `esc back` never clips at 40x16.
- Conversation length (`compact*` backend owned by context-builder;
  UI calls only `compact_load_config/effective/set_default/
  set_agent/run`): overview (`compact_overview_rows`: title, one
  purpose line, one row per scope showing only On/Off plus
  `Using … settings`/`Custom settings`, run-now, Back — no raw
  value grid) → one scope (`compact_scope_rows`: pinned
  `Applies to all … using these defaults` line, Automatic
  summaries, When to summarize, Advanced settings, always-visible
  `Use … settings` reset on per-agent scopes, Back) → On/Off
  picker / named presets (`COMPACT_PRESETS`: Standard matches the
  role defaults — orch 30 turns/~20k tokens/keep 10, sub 20/~20k/
  keep 5; More/Less often halve/double; choice preserves On/Off,
  `compact_match_preset` else Custom) / Advanced
  (`compact_advanced_rows`: exact limits, kept turns, time limit;
  typing replaces the prefill, `Enter` saves naming the value,
  `Esc` cancels). Plain-words help per row (`compact_help_for`);
  pinned header lines stay visible; the whole wrapped selected
  option scrolls into view (`compact_disp_span`); feedback wraps
  to 2–3 lines above a fixed nav footer. `Summarize this chat now`
  runs `compact_run(force=True)` on a worker thread
  (`start_compact`); busy/failure surface, no-op explains
  `No older messages to summarize yet. The newest N turns are
  kept.`, log reload, UI never freezes.
- Context/memory: real read-only `get_agent_context` view
  (`context_screen_lines` pure flattener): assignment, sources,
  warnings, content, scrollable. Honest empty state; no fake data.
- `/help` (and `?`) lists common composer-first actions + typed slash
  usage with the canonical table — no insert-mode chords, no
  intimidating hotkey wall.

## Responsive floor

- Wide (≥110 cols): single transcript + optional agents sidebar.
  Standard (80–120) and narrow (60–80): one main pane; the slash
  popup anchors above the composer (below it when the composer sits
  at the top of a tiny screen) with the status line never covered
  (`slash_popup_rect`, pinned at 120x36/80x24/60x24/40x16).
- Declared minimum 40x16 (wordmark off, popup below composer,
  transcript ~10 rows, all actions reachable). Below it: a clean
  `terminal too small — need 40x16 (have WxH) · resize or q` message;
  any resize recovers on the next frame. No unconditional expensive
  redraw (render on input/data/120ms tick; popup/lists bounded).

## Clutter audit (this round)

- Border depth: zero full-screen frames; dialogs are single charcoal
  fills (`draw_panel`), composer/bubbles are fills with a one-cell
  `▍` rule — never boxes-in-boxes.
- Signals per state: running = `●` dot + header spinner + `working
  Ns` (presence/activity/elapsed — three distinct facts, kept);
  selection = `›` + sel color/reverse (mono-safe pair, kept); error =
  `!` + alert color (kept); diff = `+-` + green/red (kept); tool card
  = status word with color reinforcement (kept).
- Always-on markers: `○`/`●` on every row encodes idle/running
  (binary state, kept); `·` separators kept minimal.
- Chrome: session ≈ 4/36 rows (header/composer×2/status); home pays
  wordmark + hint rows for the OpenCode look (intentional air).
- Removed: `i`-to-type hints/placeholder, the chord-wall hint line,
  `Type…` vagueness, `Q`-quit advertising, slash shadowing typed args.

## Ownership

- UI owner (this brief): `ag` themes/render helpers/TUI/`KEYS_HELP`,
  slash table + popup + choosers + timeline *display*
  (`chat_screen_lines` timeline roles, `timeline_*` pure helpers,
  `onevent` timeline branch), `tests/test_ag_ui.py`, README TUI
  sections, this doc, `docs/ui-previews/`,
  `scripts/make_ui_previews.py` (QA generator).
- Stream-v2 owner (do not touch): timeline *engine*
  (`parse_timeline_*`, `TimelineBuffer`, `tl_*` helpers, `run_turn`
  emission/persistence, `backend_argv`, legacy parsers, chat-log
  helpers), `tests/test_ag_streaming.py`, `docs/STREAMING.md`.
- Context owner (do not touch): context helpers near `eff_system`,
  `run_turn` hook incl. `compact_maybe/_compact_core/do_compact`,
  CLI parser + `do_context`/`do_compact`, `tests/test_ag_context.py`,
  `docs/AGENT_CONTEXT.md`, `docs/AUTO_COMPACTION.md`, root
  `custom.md`, `docs/examples/`, `docs/templates/`.
- Shared read-only: `compact_*` + `get_agent_context` stable APIs.

## Validation

- `python3 ag selfcheck` — green (run 2026-09-15; the older
  stream-v2-stale abort note no longer applies: backend selfcheck
  passes).
- `python3 tests/test_ag_ui.py` — owned by menus-review this round
  (new menus contract: overview/scope/preset/advanced, parent-menu
  return, fixed narrow footers, no invented Running status).
- Other suites untouched & green at last run: native/cli/profiles/
  profile_native/guard/wake (re-run before delivery).
- Visual QA: `docs/ui-previews/*.png` regenerated from ACTUAL
  renderer frames (`scripts/make_ui_previews.py`: 256-color stubs,
  intended frame shot BEFORE quit keys = last marker frame, semantic
  marker asserted,   `xml:space=preserve` SVG → `rsvg-convert`).
  Richer seed: 4 agents/2 projects, legacy 7-message convo + a
  timeline convo (reasoning/tool card/diff/error roles with
  event_ids); length scenes: `length-overview-100x30/80x24/40x16`
  (narrow pins the purpose with This-agent selected),
  `length-scope-100x30`, `length-onoff-80x24`,
  `length-preset-100x30/40x16` (narrow highlights More often so
  whole exact effects must fit), `length-advanced-80x24`, plus
  `slash-100x30`, `slash-filter-80x24`,
  `session-timeline-120x36/80x24/60x24/40x16` (PgUp reveals the upper
  rows at the floor sizes), `home-60x24` (skill floor),
  `session-timeline-nocolor-80x24` (`NO_COLOR`: no surfaces, reverse
  selection + words carry meaning) and `session-ascii-80x24`
  (`AG_ASCII` twins).
  Inspected with image tool: home/session/slash/timeline at wide and
  40x16, palette/config/length views/context.

## QA defects found and fixed (this round)

1. `fill(yy,x0,W,attr)` arg swap painted userfill attr as width —
   huge allocations on color terminals. Fixed to
   `fill(yy,x0,1,W,attr)`; covered by `test_bounded_color_surfaces`
   (strict on-screen assert + surface-role assert).
2. Preview harness appended `q` after the shot: session PNG was HOME,
   palette query had stray `q`. Now: shoot → append quit keys →
   join; marker asserted per scene.
3. SVG dropped leading spaces (rows shifted to x=0):
   `xml:space="preserve"` + explicit x.
4. Text attrs repainted fills with terminal bg: new `text_c`/
   `dim_c`/`text_u`/`alert_c` pairs (fg on surface bg).
5. Abyss user bubble was saturated green (bg 22): now neutral charcoal
   235 (`userfill`/`text_u`) with blue `▍` left rule; covered by
   `test_charcoal_userfill_and_no_jargon`.
6. Compact selection indexed logical rows but rendered wrapped display
   rows: now logical-id based (`compact_selectable/_first/_step/
   _disp_index`), headers/stats skipped, first editable on open;
   existing compact tests step the selectable list
   (`sel_steps`); covered by narrow wrap + skip tests.
7. Home rewound to the group header and re-rendered prior agents,
   hiding far-down selections: now an agent window with per-group
   headers + front-trim; covered by
   `test_home_long_list_keeps_selected_visible` (15 agents, 40x16/80x24).
8. `i` undiscoverable: `Ask anything… (i to type)` placeholder +
   `i ask · …` hint; `chars//4`/`off-thread` jargon removed from
   compact rows + README (backend `est_note` untouched).

## QA defects found and fixed (slash-v2 round, 2026-09-15)

1. `parse_slash` rejected `-` in command words: `/compact-now`
   fell through to a model turn (user row leaked). Widened to
   letters/digits/`-`/`_` (backslash-path safety kept); covered by a
   selfcheck assert + the compact-now TUI test.
2. `/quit` never exited: `want_quit` was checked after the `-1`
   idle-continue, so an exhausted key queue idled forever. Now
   checked first in both home and chat loops.
3. Omission marker lost its `omit` kind (re-tupled as `body`):
   assistant/reasoning/error branches now preserve it.
4. Harness exports `NO_COLOR=1`: render tests scrub it at import and
   pin the default surface; monochrome covered by its own test.
5. Timeline tool cards doubled the name (`Grep Grep …`): engine text
   already composed as `Name args`; reused as-is when it starts with
   the tool name.

## QA defects found and fixed (finish round, 2026-09-15)

1. Executed slash actions never left the composer: popup `Enter` went
   through `run_slash_entry` (not the typing `Enter` path that clears
   the buffer), so `/config` survived navigation and glued onto later
   typing (`/config/quit` reached the model as prose). Now
   `run_slash_entry` consumes the exact line from the originating
   composer before `send_active` navigates; `Esc` dismissal and dialog
   cancel still preserve drafts.
2. `parse_slash` glued-word gap: `/config/quit` fell through the regex
   to model prose. Now a `/` glued to a KNOWN word parses as a
   malformed command (local error); unknown-first-word paths
   (`/Users/me/file`) stay prose. `SLASH_KNOWN` gained `compact-now`
   so every table entry parses under both prefixes.
3. `/compact-now` printed a fake started message on refused runs.
   `start_compact` returns `started` vs the refusal text; the caller
   shows the honest outcome.
4. Plain-text submit cleared the draft even when the agent was busy.
   `send_active` returns `False` on refusal; the typing path keeps the
   draft with a visible error.
5. `_cell_clusters` glued the ZWJ into the previous cluster before the
   join step ran, splitting family emoji into 4 clusters. ZWJ now joins
   only via the dedicated step; skin-tone modifiers glue too. All
   message rows wrap by cells (`wrap_chat_line` delegates to
   `cell_wrap_line`).
6. TUI log loads sliced `load_chat_log(...)[-60:]` before dedup.
   `load_chat_view` dedups first (legacy no-`event_id` rows kept) over
   the shared loader; init/exit/compact reloads all use it.
7. The `timeline_summary` aggregate hid whenever ANY assistant row
   existed. Now same-turn only (after the nearest preceding user row).
   Live legacy `tool`/`toolout` lines skip the transcript while the
   live store already carries tool rows (no card + legacy double-show).
8. Cursor was `curs_set(0)` always: now shown at composer/edit focus
   (cell-measured), hidden in navigation/dialogs. `do_tui` maps
   `KeyboardInterrupt` to exit 130 after wrapper cleanup.

## Handoff notes

- Regenerate previews after any render change:
  `python3 scripts/make_ui_previews.py`
  (writes `docs/ui-previews/`, needs `rsvg-convert`).
- Open threads: native TUI bypasses `run_turn` (no auto trigger
  there — backend keeps its own context; see AUTO_COMPACTION.md);
  context `text` can exceed small panels (scrolls; fine).
- Mouse is best-effort: click selects rows/popup/composer and a
  second click confirms; wheel scrolls the transcript; unsupported
  terminals ignore it silently. Keyboard does everything.
- `AG_ASCII=1` swaps UI glyphs to ASCII; `NO_COLOR=1` zeroes color
  pairs (selection → reverse, errors → `!`, diffs → `+-`, tools →
  status words). Preview/test harnesses scrub both to pin the
  default surface.
- No commits made; concurrent `ag` changes preserved via
  small exact edits only.
