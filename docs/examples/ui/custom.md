# custom.md — example scoped guidance (ui scope)

Scope: chat panel + TUI rendering for this project. Agents assigned here own
the interactive surface; backend/plumbing agents own the rest.

## Design authority

- Skill: `.claude/skills/tui-design/SKILL.md` (workflow + cross-cutting
  contracts). Load `references/ecosystem-python.md` before framework or
  testing claims (stdlib curses here, not Textual), plus
  `references/visual-patterns.md` (cell widths, clutter audit, 60-col
  floor) and `references/interaction-patterns.md` (keys, focus,
  discoverability) for their domains.
- Full-screen session contract: alternate screen, stable spatial model,
  restore on every exit path (`Ctrl+C` → 130). Never block the UI/event
  thread; poll via non-blocking reads.

## Composer-first / menu contract

- Boot composing (`homeinput`/`input`): ordinary letters are text, never
  actions. `Esc` selects, any text returns to the composer.
- Leading `/` opens the searchable slash popup (`SLASH_COMMANDS` is the
  single table driving popup + `/help`): filter in place, `↑↓` move
  (window follows via `slash_visible_window`), `Tab` completes without
  running, `Enter` executes AND consumes the line from the originating
  composer first, `Esc`/backspace-past-`/` dismiss keeping the draft.
- `parse_slash` fires only on a leading slash; `//` stays prose;
  words allow `-`/`_`; a `/` glued to a KNOWN word errors locally,
  unknown-first-word paths stay user input. Slash never reaches the
  model; refused submits (busy) keep the draft with a visible error.
- Measure terminal cells, not chars (`cell_len`/`cell_trunc`/
  `cell_wrap_line` over grapheme-glued clusters). Cursor visible at
  composer/edit focus, hidden in dialogs. `NO_COLOR` keeps meaning on
  words/markers; `AG_ASCII` swaps glyphs (default Unicode untouched).
- Drill-down menus (Conversation length et al): overview → one scope
  → picker/Advanced; `Enter` opens, `Esc` steps exactly one level
  back, explicit `Back` rows everywhere; dialogs erase before drawing
  so callers never leak through; footers shrink by measured cells
  (`foot_text`) so `Esc back` survives 40x16; help wraps 2–3 lines,
  feedback wraps above a fixed footer; typing in a numeric field
  replaces the prefill, `Enter` saves naming the value.

## Rules

- Match existing style; minimal diffs; verify with `python3 tests/test_ag_ui.py`.
- Never block the main thread on backend turns; poll via non-blocking reads.
- Key handling: arrows/keys stay keys, non-ASCII text stays text.
- Test pyramid: pure unit (parsing, filter, widths, scroll math) first,
  then pinned-size fake-screen frames (incl. 60-col floor + minimum),
  then actual-renderer previews (`python3 scripts/make_ui_previews.py`).
- Ask when ambiguous; state assumptions up front.
