# claude-utils

A collection of small, dependency-free command-line utilities for working with
[Claude Code](https://claude.com/claude-code).

| Tool | Description |
| --- | --- |
| [`claude-sessions.py`](#claude-sessionspy) | list, rename, delete and preview sessions, from the CLI or a curses UI |

More tools may be added over time. Each one is a single standalone script that
only needs Python 3.9+ and the standard library.

## Installation

```sh
git clone <this repo> claude-utils
cd claude-utils
chmod +x *.py
ln -s "$PWD/claude-sessions.py" ~/.local/bin/claude-sessions   # optional
```

## Common conventions

- Claude Code's data lives in `~/.claude`; every tool honours
  `CLAUDE_CONFIG_DIR` to override it.
- Colour output can be disabled with `NO_COLOR`.
- Tools that operate on projects work on the current project by default, and
  accept `-g/--global` or `-p/--project` to change scope.

---

## claude-sessions.py

List, rename, delete and preview Claude Code sessions.

Claude Code keeps every session as a JSONL transcript under
`~/.claude/projects/`. This tool gives you a convenient way to browse and tidy
them up.

### Features

- List sessions with date, message count, title and project
- Rename sessions (same mechanism as Claude's own rename, so `/resume` shows
  the new name)
- Delete sessions, including their sidecar data (file history, session env);
  running sessions are protected
- Preview a session: dates, message counts, cost, tools used, files edited,
  first/last prompt and last reply; optionally an AI-written summary
- Curses UI with filtering, sorting, multi-select, a one-line preview next to
  each title, colours, and grouping by project in global mode

### How it relates to Claude Code's built-in features

Claude Code already has a session picker (`/resume`, or `claude --resume`
with no arguments) that covers part of this. Per the
[Claude Code docs](https://code.claude.com/docs/en/sessions.md):

| | Built in | `claude-sessions.py` |
| --- | --- | --- |
| Pick and resume a session | yes (`/resume`, `claude -r NAME`) | no (does not resume) |
| Search | yes (`/`) | yes (`/`) |
| All projects / worktrees / branch | yes (`Ctrl+A` / `Ctrl+W` / `Ctrl+B`) | all or one project (`-g`, `-p`, `t`) |
| Rename | yes (`Ctrl+R` in the picker, `/rename`) | yes |
| Preview | yes (`Space`) | yes, plus a condensed summary and an optional AI one |
| **Delete one session** | no (`claude purge` removes a whole project) | **yes**, with its sidecar data |
| **Bulk operations** (multi-select) | no | **yes** |
| **Sorting** (date, size, title, ...) | no | **yes** |
| **Group by project** | no | **yes** |
| **Mark running sessions** | no (only `bg` for background ones) | **yes** |
| **Scriptable output** (`--json`) | no | **yes** |

If you only need to find, rename or resume a session, use the built-in picker.
Reach for this tool to clean up, to get an overview across projects, or to
script against your sessions. Rename and preview are included so that
everything is available in one place.

### Usage

```
claude-sessions.py [-g | -p PATH|NAME] [--sort KEY] [COMMAND]
```

| Command | Description |
| --- | --- |
| *(none)* / `ui` | interactive curses UI (falls back to `list` if not on a terminal) |
| `list` (`ls`) | list sessions; `-n N`, `--sort`, `--json`, `--color auto\|always\|never` |
| `rename` (`mv`) `ID TITLE...` | rename a session |
| `delete` (`rm`) `ID...` | delete sessions; `-y` skips confirmation, `-n` is a dry run, `--force` allows deleting running sessions |
| `preview` (`show`) `ID` | short summary; `--ai` adds an AI summary via `claude -p --model haiku` (this sends the transcript to the API; needs the `claude` CLI on your `PATH`) |

`ID` is a session id, a unique id prefix, or an exact title.

**Scope.** Run from a project directory, it only shows that project's
sessions (if the directory has none, it falls back to all projects).
`-g/--global` selects all projects; `-p/--project PATH|NAME` selects another
project, given as a path or a substring of its encoded name.

### Examples

```sh
claude-sessions.py                       # UI for the current project
claude-sessions.py -g                    # UI for everything, grouped by project
claude-sessions.py -g ls --sort size -n 10
claude-sessions.py rename 4a7814e4 thumbnail performance
claude-sessions.py rm -n 4a7814e4        # dry run
claude-sessions.py show 4a7814e4 --ai
```

### UI keys

| Key | Action |
| --- | --- |
| `↑` `↓` / `j` `k`, `PgUp` `PgDn`, `g` `G` | move |
| `/` | filter (space-separated terms; matches title, prompts, project, id) |
| `s` | cycle sort order |
| `t` | toggle current project / all projects |
| `r` | rename |
| `space` | mark / unmark |
| `d` | delete the marked sessions (or the current one), after confirmation |
| `Enter` / `p` | preview (`a` inside for an AI summary) |
| `Esc` | clear filter / marks |
| `q` | quit |

### Notes

- Deleting a session is permanent.
- Renaming a session that is currently running may be overwritten by that
  session.

## License

MIT; see [LICENSE](LICENSE).
