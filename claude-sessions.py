#!/usr/bin/env python3
# Copyright (c) 2026 Alberto Griggio
# SPDX-License-Identifier: MIT
#
# Part of claude-utils. Licensed under the MIT License; see the LICENSE file
# in the project root for the full text.

"""List, rename, delete and preview Claude Code sessions.

Sessions live in ~/.claude/projects/<encoded-project-path>/<session-id>.jsonl
(override the base directory with $CLAUDE_CONFIG_DIR).

By default, when run from a project directory, only that project's sessions
are considered; use -g/--global for all projects or -p/--project to pick
another one. Without a subcommand a curses UI is started (on a terminal).
"""

import argparse
import curses
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR")
                  or Path.home() / ".claude")
PROJECTS_DIR = CONFIG_DIR / "projects"


# --------------------------------------------------------------------------
# Data layer
# --------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    path: Path
    project_dir: str                 # encoded directory name
    project_path: str = ""           # real cwd, when known
    title: str = ""
    custom_title: bool = False
    first_prompt: str = ""
    last_prompt: str = ""
    last_reply: str = ""
    started: float = 0.0             # epoch seconds
    modified: float = 0.0
    n_user: int = 0
    n_assistant: int = 0
    cost: float = 0.0
    size: int = 0
    running: bool = False
    tools: Counter = field(default_factory=Counter)
    files: Counter = field(default_factory=Counter)

    @property
    def project_name(self):
        return self.project_path or decode_project(self.project_dir)

    def haystack(self):
        return " ".join((self.title, self.first_prompt, self.last_prompt,
                         self.project_name, self.id)).lower()


def encode_project(path):
    """Claude Code's directory naming: every non-alphanumeric char -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def decode_project(name):
    return name.replace("-", "/") if name.startswith("-") else name


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def user_text(msg):
    """Human-typed text of a user record, or '' for meta/tool entries."""
    c = msg.get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c
                      if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return ""
    c = c.strip()
    if not c or c.startswith(("<local-command", "<command-", "<system-reminder",
                              "<task-notification", "Caveat:")):
        return ""
    if c.startswith("[Request interrupted"):
        return ""
    return c


def assistant_text(msg):
    c = msg.get("content")
    if isinstance(c, list):
        return "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text").strip()
    return c.strip() if isinstance(c, str) else ""


ANSI = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "magenta": "35", "cyan": "36", "grey": "90"}


def paint(text, style, enabled):
    if not enabled or not style:
        return text
    codes = ";".join(ANSI[x] for x in style.split("+"))
    return f"\033[{codes}m{text}\033[0m"


def use_color(mode):
    if mode == "always":
        return True
    if mode == "never" or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def one_line_preview(s):
    """What the session is (currently) about, on one line."""
    return one_line(s.last_prompt or s.first_prompt or s.last_reply)


def one_line(s, n=None):
    s = " ".join(s.split())
    return s if n is None or len(s) <= n else s[:n - 1] + "…"


def parse_session(path):
    st = path.stat()
    s = Session(id=path.stem, path=path, project_dir=path.parent.name,
                modified=st.st_mtime, size=st.st_size)
    ai_title = custom = ""
    last_ts = 0.0
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return s
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            t = o.get("type")
            if t == "custom-title":
                custom = o.get("customTitle") or custom
            elif t == "ai-title":
                ai_title = o.get("aiTitle") or ai_title
            elif t == "cost-state":
                s.cost = o.get("totalCostUSD", s.cost) or s.cost
            elif t in ("user", "assistant"):
                if not s.project_path and o.get("cwd"):
                    s.project_path = o["cwd"]
                ts = parse_ts(o.get("timestamp"))
                if ts:
                    s.started = s.started or ts
                    last_ts = ts
                if o.get("isSidechain"):
                    continue
                msg = o.get("message") or {}
                if t == "user":
                    if o.get("isMeta"):
                        continue
                    txt = user_text(msg)
                    if txt:
                        s.n_user += 1
                        s.first_prompt = s.first_prompt or txt
                        s.last_prompt = txt
                else:
                    s.n_assistant += 1
                    txt = assistant_text(msg)
                    if txt:
                        s.last_reply = txt
                    c = msg.get("content")
                    for b in c if isinstance(c, list) else ():
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            s.tools[b.get("name", "?")] += 1
                            fp = (b.get("input") or {}).get("file_path")
                            if fp and b.get("name") in (
                                    "Edit", "Write", "NotebookEdit",
                                    "MultiEdit"):
                                s.files[fp] += 1
            elif not s.project_path and o.get("cwd"):
                s.project_path = o["cwd"]
    s.started = s.started or st.st_ctime
    s.custom_title = bool(custom)
    s.title = custom or ai_title or one_line(s.first_prompt, 80) or "(empty)"
    return s


def project_dirs(scope):
    """scope: None (all) or a list of encoded project dir names."""
    if not PROJECTS_DIR.is_dir():
        return []
    if scope is None:
        return sorted(p for p in PROJECTS_DIR.iterdir() if p.is_dir())
    return [PROJECTS_DIR / n for n in scope if (PROJECTS_DIR / n).is_dir()]


def load_sessions(scope):
    out = []
    live = live_session_ids()
    for d in project_dirs(scope):
        for f in d.glob("*.jsonl"):
            s = parse_session(f)
            s.running = s.id in live
            out.append(s)
    return out


def resolve_scope(args):
    """Return (scope, label): scope None for global, else list of dir names."""
    if getattr(args, "global_", False):
        return None, "all projects"
    if getattr(args, "project", None):
        p = args.project
        cand = Path(p).expanduser()
        if cand.exists():
            enc = encode_project(cand.resolve())
            if (PROJECTS_DIR / enc).is_dir():
                return [enc], str(cand.resolve())
            sys.exit(f"error: no sessions for project {cand.resolve()}")
        names = [d.name for d in project_dirs(None)]
        if p in names:
            return [p], decode_project(p)
        m = [n for n in names if p.lower() in n.lower()]
        if not m:
            sys.exit(f"error: no project matches '{p}'")
        return m, f"projects matching '{p}'"
    cwd = Path.cwd()
    for c in dict.fromkeys((cwd, cwd.resolve())):
        enc = encode_project(c)
        if (PROJECTS_DIR / enc).is_dir():
            return [enc], str(c)
    print("note: no sessions for the current directory; showing all projects",
          file=sys.stderr)
    return None, "all projects"


def live_session_ids():
    ids = set()
    for f in (CONFIG_DIR / "sessions").glob("*.json"):
        try:
            o = json.loads(f.read_text())
            os.kill(int(o["pid"]), 0)
            ids.add(o["sessionId"])
        except PermissionError:
            ids.add(o.get("sessionId"))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return ids


def find_sessions(sessions, ref):
    """Sessions whose id starts with ref, or whose title equals ref."""
    m = [s for s in sessions if s.id == ref]
    return m or [s for s in sessions if s.id.startswith(ref)] \
        or [s for s in sessions if s.title == ref]


def resolve_one(sessions, ref):
    m = find_sessions(sessions, ref)
    if not m:
        raise SystemExit(f"error: no session matches '{ref}'")
    if len(m) > 1:
        raise SystemExit(f"error: '{ref}' is ambiguous: "
                         + ", ".join(s.id[:8] for s in m))
    return m[0]


def rename_session(s, title):
    title = title.strip()
    if not title:
        raise ValueError("empty title")
    with open(s.path, "a", encoding="utf-8") as f:
        # start on a fresh line even if the file lacks a trailing newline
        if s.size and not _ends_with_newline(s.path):
            f.write("\n")
        for rec in ({"type": "custom-title", "customTitle": title,
                     "sessionId": s.id},
                    {"type": "agent-name", "agentName": title,
                     "sessionId": s.id}):
            f.write(json.dumps(rec, ensure_ascii=False,
                               separators=(",", ":")) + "\n")
    s.title, s.custom_title = title, True


def _ends_with_newline(path):
    with open(path, "rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b"\n"


def sidecar_paths(s):
    return [p for p in (s.path.parent / s.id,
                        CONFIG_DIR / "file-history" / s.id,
                        CONFIG_DIR / "session-env" / s.id) if p.exists()]


def delete_session(s):
    for p in sidecar_paths(s):
        shutil.rmtree(p, ignore_errors=True)
    s.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Preview / summary
# --------------------------------------------------------------------------

def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def fmt_size(n):
    for u in ("B", "K", "M", "G"):
        if n < 1024 or u == "G":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024


def summary_lines(s, width=78):
    """Offline heuristic summary as a list of lines."""
    def wrap(label, text, n=400):
        if not text:
            return []
        return textwrap.wrap(one_line(text, n), width,
                             initial_indent=f"{label}: ",
                             subsequent_indent="    ")
    out = [f"Title:    {s.title}",
           f"ID:       {s.id}" + ("    ● running" if s.running else ""),
           f"Project:  {s.project_name}",
           f"Started:  {fmt_time(s.started)}"
           f"    Last activity: {fmt_time(s.modified)}",
           f"Messages: {s.n_user} prompts, {s.n_assistant} replies"
           f"    Size: {fmt_size(s.size)}"
           + (f"    Cost: ${s.cost:.2f}" if s.cost else "")]
    if s.tools:
        out.append("Tools:    " + ", ".join(
            f"{k}×{v}" for k, v in s.tools.most_common(6)))
    if s.files:
        top = [os.path.basename(f) for f, _ in s.files.most_common(5)]
        out.append(f"Edited:   {len(s.files)} file(s): " + ", ".join(top))
    out.append("")
    out += wrap("First prompt", s.first_prompt)
    if s.n_user > 1:
        out += wrap("Last prompt ", s.last_prompt)
    out += wrap("Last reply  ", s.last_reply, 500)
    return out


def transcript_excerpt(s, limit=12000):
    """Condensed transcript for the AI summary."""
    parts = []
    with open(s.path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if (o.get("type") not in ("user", "assistant")
                    or o.get("isSidechain") or o.get("isMeta")):
                continue
            msg = o.get("message") or {}
            if o["type"] == "user":
                t = user_text(msg)
                if t:
                    parts.append("USER: " + one_line(t, 600))
            else:
                t = assistant_text(msg)
                if t:
                    parts.append("ASSISTANT: " + one_line(t, 400))
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit // 2] + "\n[...]\n" + text[-limit // 2:]
    return text


def ai_summary(s):
    prompt = ("Summarize this Claude Code session in 3-4 short lines: what "
              "the user wanted, what was done, and the current state. "
              "No preamble.\n\n"
              + transcript_excerpt(s))
    try:
        r = subprocess.run(["claude", "-p", "--model", "haiku", prompt],
                           capture_output=True, text=True, timeout=120, cwd="/")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(AI summary failed: {e})"
    return r.stdout.strip() or f"(AI summary failed: {r.stderr.strip()[:200]})"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

SORTS = {
    "modified": (lambda s: s.modified, True),
    "created": (lambda s: s.started, True),
    "size": (lambda s: s.size, True),
    "title": (lambda s: s.title.lower(), False),
    "messages": (lambda s: s.n_user, True),
}


def sort_sessions(sessions, key):
    f, rev = SORTS[key]
    return sorted(sessions, key=f, reverse=rev)


def cmd_list(args, sessions, scope_global):
    sessions = sort_sessions(sessions, args.sort)
    if args.limit:
        sessions = sessions[:args.limit]
    if args.json:
        json.dump([{"id": s.id, "title": s.title, "project": s.project_name,
                    "started": fmt_time(s.started),
                    "modified": fmt_time(s.modified),
                    "prompts": s.n_user, "replies": s.n_assistant,
                    "cost": s.cost, "size": s.size, "running": s.running,
                    "path": str(s.path)}
                   for s in sessions], sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    if not sessions:
        print("no sessions")
        return
    width = shutil.get_terminal_size((120, 24)).columns
    home = str(Path.home())
    rows = []
    for s in sessions:
        proj = s.project_name.replace(home, "~", 1)
        rows.append((s.id[:8], fmt_time(s.modified), str(s.n_user), s.title,
                     proj))
    fixed = 2 + 8 + 16 + 5 + 3 * 2 + (0 if not scope_global else 2)
    avail = max(20, width - fixed)
    tw = avail if not scope_global else avail * 6 // 10
    pw = avail - tw - 2
    col = use_color(getattr(args, "color", "auto"))
    head = f"  {'ID':8}  {'MODIFIED':16}  {'MSGS':>4}  {'TITLE':{tw}}"
    if scope_global:
        head += "  PROJECT"
    print(paint(head.rstrip(), "bold", col))
    for sess, (i, m, n, t, p) in zip(sessions, rows):
        t = one_line(t, tw)
        parts = [paint(f"{i:8}", "yellow", col),
                 paint(f"{m:16}", "cyan", col),
                 paint(f"{n:>4}", "blue", col),
                 paint(f"{t:{tw}}" if scope_global else t,
                       "bold+green" if sess.custom_title else "", col)]
        if scope_global:
            p = p[-pw:] if len(p) > pw else p
            parts.append(paint(one_line(p, pw), "magenta", col))
        run = paint("●", "bold+green", col) if sess.running else " "
        print(run + " " + "  ".join(parts))


def cmd_rename(args, sessions, _):
    s = resolve_one(sessions, args.session)
    old = s.title
    rename_session(s, " ".join(args.title))
    print(f"renamed {s.id[:8]}: {old!r} -> {s.title!r}")
    if s.running:
        print("warning: session is currently running; "
              "it may overwrite the title", file=sys.stderr)


def cmd_delete(args, sessions, _):
    targets = []
    for ref in args.sessions:
        m = find_sessions(sessions, ref)
        if not m:
            sys.exit(f"error: no session matches '{ref}'")
        if len(m) > 1:
            sys.exit(f"error: '{ref}' is ambiguous: "
                     + ", ".join(x.id[:8] for x in m))
        if m[0] not in targets:
            targets.append(m[0])
    for s in targets:
        print(f"{s.id[:8]}  {fmt_time(s.modified)}  {s.title}  "
              f"[{fmt_size(s.size)}]"
              + ("  (RUNNING)" if s.running else ""))
    running = [s for s in targets if s.running]
    if running and not args.force:
        sys.exit("error: refusing to delete running session(s); use --force")
    if args.dry_run:
        for s in targets:
            for p in [s.path] + sidecar_paths(s):
                print("would remove", p)
        return
    if not args.yes:
        ans = input(f"Delete {len(targets)} session(s)? [y/N] ")
        if ans.strip().lower() != "y":
            print("aborted")
            return
    for s in targets:
        delete_session(s)
    print(f"deleted {len(targets)} session(s)")


def cmd_preview(args, sessions, _):
    s = resolve_one(sessions, args.session)
    print("\n".join(summary_lines(s)))
    if args.ai:
        print("\nAI summary:")
        print(textwrap.fill(ai_summary(s), 78, replace_whitespace=False))


# --------------------------------------------------------------------------
# Curses UI
# --------------------------------------------------------------------------

LABEL_RE = re.compile(r"^(Title|ID|Status|Project|Started|Messages|Tools|"
                      r"Edited|First prompt|Last prompt |Last reply  |"
                      r"AI summary):")


def cwidth(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def fit(text, width):
    """Truncate/pad text to exactly `width` display cells."""
    out, w = [], 0
    for ch in text:
        cw = cwidth(ch)
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(out) + " " * (width - w)


class UI:
    def __init__(self, scr, args, scope, label):
        self.scr = scr
        self.args = args
        self.scope, self.label = scope, label
        self.default_scope = (scope, label)
        self.sort = args.sort
        self.filter = ""
        self.sel = 0
        self.top = 0
        self.marked = set()
        self.msg = ""
        self.sessions = []
        self.attr = {}
        self.init_colors()
        self.reload()

    def init_colors(self):
        names = ["date", "count", "custom", "project", "mark", "msg",
                 "label", "preview", "running"]
        self.attr = {n: 0 for n in names}
        self.attr["preview"] = curses.A_DIM
        self.attr["custom"] = curses.A_BOLD
        self.attr["mark"] = curses.A_BOLD
        self.attr["running"] = curses.A_BOLD
        self.attr["msg"] = curses.A_BOLD
        self.attr["label"] = curses.A_BOLD
        if os.environ.get("NO_COLOR") or not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            grey = 8 if curses.COLORS >= 16 else -1
            spec = {"date": curses.COLOR_CYAN, "count": curses.COLOR_BLUE,
                    "custom": curses.COLOR_GREEN,
                    "project": curses.COLOR_MAGENTA,
                    "mark": curses.COLOR_RED, "msg": curses.COLOR_YELLOW,
                    "label": curses.COLOR_CYAN, "preview": grey,
                    "running": curses.COLOR_GREEN}
            for n, (name, fg) in enumerate(spec.items(), 1):
                curses.init_pair(n, fg, -1)
                self.attr[name] |= curses.color_pair(n)
            if grey != -1:
                self.attr["preview"] &= ~curses.A_DIM
        except curses.error:
            pass

    # -- state ----------------------------------------------------------
    def reload(self):
        self.sessions = load_sessions(self.scope)
        self.marked &= {s.id for s in self.sessions}

    def view(self):
        v = sort_sessions(self.sessions, self.sort)
        for term in self.filter.lower().split():
            v = [s for s in v if term in s.haystack()]
        if self.is_global:
            # group by project; groups ordered by their first session
            groups = {}
            for s in v:
                groups.setdefault(s.project_name, []).append(s)
            v = [s for g in groups.values() for s in g]
        return v

    @property
    def is_global(self):
        return self.scope is None or len(self.scope) > 1

    # -- drawing --------------------------------------------------------
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w:
            try:
                self.scr.addstr(y, x, fit(text, w - x), attr)
            except curses.error:
                pass

    def put_segments(self, y, segs, extra=0):
        """Draw (text, attr) segments left to right."""
        w = self.scr.getmaxyx()[1]
        x = 0
        for text, attr in segs:
            if x >= w:
                break
            self.put(y, x, text, attr | extra)
            x += sum(cwidth(c) for c in text)

    def draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        v = self.view()
        self.sel = max(0, min(self.sel, len(v) - 1))
        body = h - 3
        # display rows: project headers (in global mode) and sessions
        rows, sel_row = [], 0
        counts = Counter(s.project_name for s in v)
        for idx, s in enumerate(v):
            new_group = (idx == 0
                         or v[idx - 1].project_name != s.project_name)
            if self.is_global and new_group:
                rows.append((None, s.project_name))
            if idx == self.sel:
                sel_row = len(rows)
            rows.append((idx, s))
        if self.sel < len(v) and sel_row > 0 and rows[sel_row - 1][0] is None:
            sel_row_top = sel_row - 1       # keep the group header visible
        else:
            sel_row_top = sel_row
        if sel_row_top < self.top:
            self.top = sel_row_top
        if sel_row >= self.top + body:
            self.top = sel_row - body + 1
        self.top = max(0, self.top)

        flt = f"  filter: {self.filter}" if self.filter else ""
        self.put(0, 0, f" claude sessions — {self.label} — "
                 f"{len(v)}/{len(self.sessions)} — sort: {self.sort}{flt}",
                 curses.A_REVERSE | curses.A_BOLD)
        home = str(Path.home())
        ind = 2 if self.is_global else 0
        tc = max(10, w - 26 - ind)
        longest = max((sum(cwidth(c) for c in s.title) for s in v), default=0)
        tw = max(10, min(longest, tc * 55 // 100))
        prw = tc - tw - 2
        for i in range(body):
            if self.top + i >= len(rows):
                break
            idx, s = rows[self.top + i]
            A = self.attr
            if idx is None:
                name = s.replace(home, "~", 1)
                self.put_segments(1 + i, [
                    (f" {name} ", A["project"] | curses.A_BOLD),
                    (f"({counts[s]})", A["preview"])])
                continue
            mark = "*" if s.id in self.marked else " "
            segs = [(" " * ind, 0), (mark, A["mark"]),
                    ("●" if s.running else " ", A["running"]), (" ", 0),
                    (fmt_time(s.modified) + " ", A["date"]),
                    (f"{s.n_user:>4}  ", A["count"]),
                    (fit(s.title, tw), A["custom"] if s.custom_title else 0)]
            if prw >= 8:
                segs.append(("  " + fit(one_line_preview(s), prw),
                             A["preview"]))
            else:
                segs.append((" " * (tc - tw), 0))
            extra = curses.A_REVERSE if idx == self.sel else 0
            self.put(1 + i, 0, " " * w, extra)
            self.put_segments(1 + i, segs, extra)
        if not v:
            self.put(2, 2, "no sessions"
                     + (" match the filter" if self.filter else ""))
        keys = (" ↑↓ move  / filter  s sort  t scope  r rename  d delete"
                "  space mark  Enter preview  ● running  q quit")
        self.put(h - 2, 0, keys, curses.A_DIM)
        self.put(h - 1, 0, " " + self.msg, self.attr["msg"])
        scr.refresh()
        return v

    # -- helpers --------------------------------------------------------
    def prompt(self, label, initial=""):
        """Single-line input at the bottom; returns None on Esc."""
        buf = list(initial)
        curses.curs_set(1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                text = label + "".join(buf)
                self.put(h - 1, 0, text, curses.A_BOLD)
                self.scr.move(h - 1, min(w - 1, sum(cwidth(c) for c in text)))
                self.scr.refresh()
                k = self.scr.get_wch()
                if k in ("\n", "\r") or k == curses.KEY_ENTER:
                    return "".join(buf)
                if k == "\x1b":
                    return None
                if k in ("\x7f", "\b") or k == curses.KEY_BACKSPACE:
                    if buf:
                        buf.pop()
                elif k == "\x15":            # ^U
                    buf.clear()
                elif isinstance(k, str) and k.isprintable():
                    buf.append(k)
                elif k == curses.KEY_RESIZE:
                    self.scr.erase()
                    self.draw()
        finally:
            curses.curs_set(0)

    def confirm(self, text):
        h, _ = self.scr.getmaxyx()
        self.put(h - 1, 0, f" {text} [y/N] ", curses.A_BOLD)
        self.scr.refresh()
        k = self.scr.get_wch()
        return isinstance(k, str) and k.lower() == "y"

    def show_text(self, title, lines_fn):
        """Scrollable text viewer; lines_fn(width) -> list of lines."""
        pos = 0
        while True:
            h, w = self.scr.getmaxyx()
            lines = lines_fn(max(20, w - 3))
            self.scr.erase()
            self.put(0, 0, f" {title}", curses.A_REVERSE)
            for i, ln in enumerate(lines[pos:pos + h - 2]):
                m = LABEL_RE.match(ln)
                if m:
                    n = m.end()
                    self.put_segments(
                        1 + i, [(" " + ln[:n], self.attr["label"]),
                                (ln[n:], 0)])
                else:
                    self.put(1 + i, 1, ln)
            self.put(h - 1, 0, " ↑↓ scroll  a AI summary  q/Esc back",
                     curses.A_DIM)
            self.scr.refresh()
            k = self.scr.get_wch()
            if k in ("q", "\x1b", "\n") :
                return
            if k == "a":
                return "ai"
            if k in (curses.KEY_DOWN, "j", " ", curses.KEY_NPAGE):
                step = h - 2 if k in (" ", curses.KEY_NPAGE) else 1
                pos = min(max(0, len(lines) - (h - 2)), pos + step)
            elif k in (curses.KEY_UP, "k", curses.KEY_PPAGE):
                pos = max(0, pos - (h - 2 if k == curses.KEY_PPAGE else 1))

    def preview(self, s):
        ai = None
        while True:
            def lines(width, s=s):
                out = summary_lines(s, width)
                if ai:
                    body = ai.splitlines() if "\n" in ai \
                        else textwrap.wrap(ai, width)
                    out += ["", "AI summary:"] + body
                return out
            r = self.show_text(f"{s.title}", lines)
            if r != "ai":
                return
            self.msg = "asking claude…"
            self.put(self.scr.getmaxyx()[0] - 1, 0,
                     " asking claude (haiku)…", curses.A_BOLD)
            self.scr.refresh()
            ai = ai_summary(s)

    # -- main loop ------------------------------------------------------
    def run(self):
        curses.curs_set(0)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        self.scr.keypad(True)
        while True:
            v = self.draw()
            self.msg = ""
            k = self.scr.get_wch()
            cur = v[self.sel] if v else None
            h = self.scr.getmaxyx()[0]
            if k in ("q", "\x03") or (k == "\x1b" and not self.filter):
                if k == "\x1b" and self.marked:
                    self.marked.clear()
                    continue
                return
            elif k == "\x1b":
                self.filter = ""
            elif k in (curses.KEY_DOWN, "j"):
                self.sel += 1
            elif k in (curses.KEY_UP, "k"):
                self.sel -= 1
            elif k == curses.KEY_NPAGE:
                self.sel += h - 3
            elif k == curses.KEY_PPAGE:
                self.sel -= h - 3
            elif k in ("g", curses.KEY_HOME):
                self.sel = 0
            elif k in ("G", curses.KEY_END):
                self.sel = len(v) - 1
            elif k == "/":
                r = self.prompt("/", self.filter)
                if r is not None:
                    self.filter, self.sel = r, 0
            elif k == "s":
                keys = list(SORTS)
                self.sort = keys[(keys.index(self.sort) + 1) % len(keys)]
            elif k == "t":
                if self.scope is None:
                    self.scope, self.label = self.default_scope
                    if self.scope is None:
                        self.scope, self.label = resolve_cwd_scope()
                else:
                    self.scope, self.label = None, "all projects"
                self.reload()
                self.sel = 0
            elif k == " " and cur:
                self.marked ^= {cur.id}
                self.sel += 1
            elif k == "r" and cur:
                r = self.prompt(" new title: ", cur.title)
                if r is not None and r.strip():
                    try:
                        rename_session(cur, r)
                        self.msg = "renamed"
                    except (OSError, ValueError) as e:
                        self.msg = f"rename failed: {e}"
            elif k == "d" and (cur or self.marked):
                targets = [s for s in self.sessions
                           if s.id in self.marked] or [cur]
                if any(s.running for s in targets):
                    self.msg = "refusing: a selected session is running"
                    continue
                what = f"{len(targets)} sessions" if len(targets) > 1 \
                    else f"'{one_line(targets[0].title, 40)}'"
                if self.confirm(f"Delete {what}?"):
                    for s in targets:
                        delete_session(s)
                    self.marked.clear()
                    self.reload()
                    self.msg = f"deleted {len(targets)}"
            elif k in ("\n", "p", curses.KEY_ENTER, curses.KEY_RIGHT) and cur:
                self.preview(cur)


def resolve_cwd_scope():
    enc = encode_project(Path.cwd().resolve())
    if (PROJECTS_DIR / enc).is_dir():
        return [enc], str(Path.cwd())
    return None, "all projects"


def cmd_ui(args, scope, label):
    def main(scr):
        UI(scr, args, scope, label).run()
    curses.wrapper(main)


# --------------------------------------------------------------------------

def build_parser():
    scope = argparse.ArgumentParser(add_help=False)
    g = scope.add_argument_group(
        "scope (default: the current project, if it has sessions)")
    g.add_argument("-g", "--global", dest="global_", action="store_true",
                   default=argparse.SUPPRESS,
                   help="operate on sessions of all projects")
    g.add_argument("-p", "--project", metavar="PATH|NAME",
                   default=argparse.SUPPRESS,
                   help="operate on another project (a path, or a substring "
                        "of the encoded project name)")
    ap = argparse.ArgumentParser(
        description=__doc__, parents=[scope],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sort", choices=SORTS, default="modified",
                    help="initial sort order (default: modified)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", aliases=["ls"], parents=[scope],
                       help="list sessions")
    p.add_argument("--sort", choices=SORTS, default=argparse.SUPPRESS)
    p.add_argument("-n", "--limit", type=int, help="show at most N sessions")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.add_argument("--color", choices=("auto", "always", "never"),
                   default="auto", help="colorize output (default: auto; "
                   "honours NO_COLOR)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("rename", aliases=["mv"], parents=[scope],
                       help="rename a session")
    p.add_argument("session",
                   help="session id (or unique prefix) or exact title")
    p.add_argument("title", nargs="+", help="new title")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("delete", aliases=["rm"], parents=[scope],
                       help="delete sessions")
    p.add_argument("sessions", nargs="+", metavar="session")
    p.add_argument("-y", "--yes", action="store_true",
                   help="don't ask for confirmation")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="only show what would go")
    p.add_argument("--force", action="store_true",
                   help="allow deleting running sessions")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("preview", aliases=["show"], parents=[scope],
                       help="show a short summary of a session")
    p.add_argument("session")
    p.add_argument("--ai", action="store_true",
                   help="also ask `claude -p` (haiku) for a summary; sends the "
                        "transcript to the API")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("ui", parents=[scope],
                       help="interactive curses UI (the default)")
    p.add_argument("--sort", choices=SORTS, default=argparse.SUPPRESS)
    p.set_defaults(func=None)
    return ap


def main():
    args = build_parser().parse_args()
    scope, label = resolve_scope(args)
    if args.cmd in (None, "ui"):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            if args.cmd == "ui":
                sys.exit("error: the UI needs a terminal")
            args.sort, args.limit, args.json = args.sort, None, False
            return cmd_list(args, load_sessions(scope),
                            scope is None or len(scope) > 1)
        return cmd_ui(args, scope, label)
    sessions = load_sessions(scope)
    args.func(args, sessions, scope is None or len(scope) > 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
