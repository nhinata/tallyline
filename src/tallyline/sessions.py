"""`tallyline sessions`: open Claude Code sessions and what resuming each one costs.

Claude Code writes one JSON file per running session to ~/.claude/sessions/<pid>.json
(session id, working directory, status, tmux pane). This is undocumented, so every field is
read defensively. Titles, the last message time and the context size come from the
session's transcript.

A session left idle past its cache lifetime (5 minutes or 1 hour, whichever the session
last wrote) rewrites its whole context on the next message, which is the resume cost shown.
"""
import json
import os
import subprocess
import time
import unicodedata
from datetime import datetime

from . import paths, pricing
from .render import fmt_context, fmt_usd

TAIL_BYTES = 4 << 20  # the latest usage and timestamps live at the end of a transcript
LIFETIME = {"1h": 3600, "5m": 300}
WINDOWS = os.name == "nt"


def _alive(pid):
    if WINDOWS:
        # On Windows os.kill(pid, 0) terminates the process instead of probing it, so
        # trust the session file there; a crashed session may linger in the list.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _process_table():
    """{pid: (ppid, tty)} for every process, from a single ps call."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,tty="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "?")
    return table


def _ancestors(table, pid=None):
    pid = pid or os.getpid()
    seen = set()
    while pid and pid not in seen and pid in table:
        seen.add(pid)
        pid = table[pid][0]
    return seen


def _tmux_panes():
    """{"@window.%pane": "session:index name"} for every tmux pane, or {} without tmux."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{window_id}.#{pane_id}\t#S:#I\t#W"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    panes = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            panes[parts[0]] = (parts[1], parts[2])
    return panes


def _ts(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _empty_info():
    return {"title": None, "last": None, "context": 0, "model": None, "speed": "standard",
            "ttl": None}


def read_transcript(path):
    """Title, last message time, current context size, model, speed and cache lifetime."""
    info = _empty_info()
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            for line in f:  # titles can be rewritten anywhere in the file
                if b'"ai-title"' in line:
                    try:
                        info["title"] = json.loads(line).get("aiTitle") or info["title"]
                    except ValueError:
                        pass
            window = TAIL_BYTES
            while True:  # widen the tail until it holds a response with usage
                f.seek(max(0, size - window))
                tail = f.read().splitlines()
                if size > window:
                    tail = tail[1:]  # the first line is cut mid-way
                _read_tail(tail, info)
                if info["context"] or window >= size:
                    break
                window *= 4
    except OSError:
        pass
    return info


def _read_tail(lines, info):
    for line in lines:
        if b'"timestamp"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") not in ("user", "assistant") or o.get("isSidechain"):
            continue
        info["last"] = max(info["last"] or 0, _ts(o.get("timestamp")) or 0) or info["last"]
        msg = o.get("message") or {}
        u = msg.get("usage")
        if o["type"] != "assistant" or not u:
            continue
        ctx = sum(u.get(k) or 0 for k in
                  ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if not ctx:  # synthetic messages carry no usage
            continue
        info.update(context=ctx, model=msg.get("model"), speed=u.get("speed") or "standard")
        cc = u.get("cache_creation") or {}
        if cc.get("ephemeral_1h_input_tokens"):
            info["ttl"] = "1h"
        elif cc.get("ephemeral_5m_input_tokens"):
            info["ttl"] = "5m"


def find_transcript(session_id):
    for path in paths.projects_dir().glob(f"*/{session_id}.jsonl"):
        return path
    return None


def collect(prices, now=None, table=None, panes=None):
    """Open sessions, most recently active first."""
    now = now or time.time()
    table = _process_table() if table is None else table
    panes = _tmux_panes() if panes is None else panes
    mine = _ancestors(table)
    sessions = []
    for f in sorted((paths.claude_dir() / "sessions").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            pid = int(d["pid"])
            sid = d["sessionId"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not _alive(pid):
            continue  # left behind by a session that crashed
        t = find_transcript(sid)
        info = read_transcript(t) if t else _empty_info()
        if d.get("tmux"):
            pane = panes.get(d["tmux"].split(":", 1)[-1])
            where = {"kind": "tmux", "target": pane[0] if pane else d["tmux"].split(":")[0],
                     "name": pane[1] if pane else None}
        elif d.get("kind") == "bg":
            where = {"kind": "background", "target": None, "name": None}
        else:
            where = {"kind": "tty", "target": table.get(pid, (0, "?"))[1], "name": None}
        last = info["last"] or (int(d.get("updatedAt") or 0) / 1000) or None
        idle = now - last if last else None
        ttl = info["ttl"] or "5m"
        expired = d.get("status") != "busy" and (idle is None or idle > LIFETIME[ttl])
        cost = None
        if expired and info["context"]:
            r = pricing.rates(prices, info["model"], info["speed"])
            cost = info["context"] * r[f"cache_write_{ttl}"] if r else None
        sessions.append({
            "pid": pid, "id": sid, "cwd": d.get("cwd"), "status": d.get("status") or "?",
            "where": where, "title": info["title"] or d.get("name"), "last_message": last,
            "idle_seconds": idle, "context": info["context"], "model": info["model"],
            "cache": "expired" if expired else "warm", "cache_lifetime": ttl,
            "resume_cost": cost, "this_session": pid in mine,
            "job_id": d.get("jobId"), "parked_job_id": d.get("parkedJobId"),
        })
    sessions.sort(key=lambda s: -(s["last_message"] or 0))
    for n, s in enumerate(sessions, 1):
        s["n"] = n
    by_job = {s["job_id"]: s["n"] for s in sessions if s["job_id"]}
    for s in sessions:
        s["continued_as"] = by_job.get(s["parked_job_id"])
    return sessions


# --- output ---

def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text, width, right=False):
    gap = " " * max(0, width - _width(text))
    return gap + text if right else text + gap


def _ago(seconds):
    if seconds is None:
        return "?"
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def _where(w, show_titles):
    if w["kind"] == "tmux":
        name = f" {w['name']}" if w["name"] and show_titles else ""
        return f"tmux {w['target']}{name}"
    return "background" if w["kind"] == "background" else w["target"]


def _resume(s):
    return f"≈{fmt_usd(s['resume_cost'])}" if s["resume_cost"] is not None else ""


def _title(s, show_titles):
    title = s["title"] if show_titles and s["title"] else f"[{s['id'][:8]}]"
    if _width(title) > 40:
        while _width(title) > 39:
            title = title[:-1]
        title += "…"
    notes = []
    if s["this_session"]:
        notes.append("this session")
    if s["continued_as"]:
        notes.append(f"continued as #{s['continued_as']}")
    return title + (f"  ({', '.join(notes)})" if notes else "")


def text_report(sessions, show_titles=True):
    if not sessions:
        return "No open Claude Code sessions."
    expired = [s for s in sessions if s["cache"] == "expired"]
    head = [f"{len(sessions)} open Claude Code session{'s' if len(sessions) != 1 else ''}"
            f" · {len(expired)} with an expired cache"]
    if expired:
        tokens = sum(s["context"] for s in expired)
        cost = sum(s["resume_cost"] or 0 for s in expired)
        head.append(f"Resuming all of them writes {fmt_context(tokens)} tokens again"
                    f" (≈{fmt_usd(cost)} API-equivalent).")
    cols = ["#", "Last message", "Where", "State", "Context", "Cache", "Resume", "Title"]
    rows = [[str(s["n"]), _ago(s["idle_seconds"]), _where(s["where"], show_titles),
             s["status"], fmt_context(s["context"]) if s["context"] else "-", s["cache"],
             _resume(s), _title(s, show_titles)] for s in sessions]
    right = {0, 4, 6}
    widths = [max(_width(r[i]) for r in rows + [cols]) for i in range(len(cols) - 1)]

    def fmt(r):
        cells = [_pad(r[i], widths[i], i in right) for i in range(len(widths))]
        return "  ".join(cells + [r[-1]]).rstrip()

    lines = head + ["", fmt(cols)] + [fmt(r) for r in rows]
    lines += ["", "Resume: an expired session writes its whole context to the cache again on the"
              " next message.", "Closing a session keeps the conversation; claude --resume"
              " picks it up later."]
    return "\n".join(lines)


def json_report(sessions, show_titles=True):
    out = []
    for s in sessions:
        s = dict(s)
        if not show_titles:
            s.update(title=None, cwd=None, where={**s["where"], "name": None})
        if s["resume_cost"] is not None:
            s["resume_cost"] = round(s["resume_cost"], 4)
        if s["idle_seconds"] is not None:
            s["idle_seconds"] = round(s["idle_seconds"])
        out.append(s)
    return json.dumps({"sessions": out}, ensure_ascii=False, indent=2)


def run(as_json=False, show_titles=True, sort="recent", prices=None, now=None,
        table=None, panes=None):
    prices = pricing.load() if prices is None else prices
    sessions = collect(prices, now=now, table=table, panes=panes)
    if sort == "resume":
        sessions.sort(key=lambda s: -(s["resume_cost"] or 0))
    return json_report(sessions, show_titles) if as_json else text_report(sessions, show_titles)
