"""`tallyline diagnose`: why usage was high, read from the transcripts themselves.

The status line answers "how much"; this answers "why". It reads transcripts directly
rather than the usage store, because the causes depend on what the store does not keep:
the order of requests, idle gaps, compactions and tool outputs. It runs only on demand,
so the status line's hot path is untouched.

Costs are API-equivalent USD. Two causes split the spend without overlapping:

- expired: cache writes on a request that followed an idle gap longer than the cache
  lifetime, i.e. the whole context written again after a break.
- tools: each tool output's share of the cache writes and reads from the request that
  first sent it until its thread ends or is compacted (expired rewrites excluded).

"Long context" is a lens over the same spend (the part of each cache read above
LONG_CONTEXT tokens), so it overlaps the causes above and is reported as such.
"""
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import paths, pricing
from .render import fmt_tokens, fmt_usd

LONG_CONTEXT = 200_000
# An idle gap counts as the cause of a rewrite only when that rewrite is substantial.
EXPIRED_MIN_TOKENS = 10_000
# The first request of a resumed transcript writes the old context from scratch; a fresh
# session's first write (system prompt, tools, CLAUDE.md) stays well below this.
RESUMED_MIN_TOKENS = 60_000
IMAGE_CHARS = 3000  # weight of an image block when splitting context growth by size
CHARS_PER_TOKEN = 3  # fallback when context growth can't be measured
REPEAT_MIN = 3
TOP = 5

ADVICE = {
    "expired": "Before a long break, /compact or start a new session.",
    "long": "/clear when the topic changes: the whole context is re-read every turn.",
    "tools": "Delegate broad reading or browsing to a subagent so only its summary stays.",
    "repeats": "Ask for notes on a file once instead of re-reading it in the same session.",
}


class Call:
    """One API request, with the tool outputs first sent in it."""

    __slots__ = ("ts", "model", "speed", "tokens", "results", "other_chars", "compacted")

    def __init__(self, ts, model, speed, tokens):
        self.ts, self.model, self.speed, self.tokens = ts, model, speed, tokens
        self.results = []       # [(label, tool, chars, file path or None)]
        self.other_chars = 0    # user text, reminders and other non-tool content
        self.compacted = False  # a compaction happened just before this request

    @property
    def ctx(self):
        t = self.tokens
        return t["input"] + t["cache_write_5m"] + t["cache_write_1h"] + t["cache_read"]


class Thread:
    """Requests of one transcript file in order (sidechains form their own thread)."""

    def __init__(self, session):
        self.session = session
        self.calls = []


def _content_chars(content):
    if isinstance(content, str):
        return len(content)
    n = 0
    for c in content if isinstance(content, list) else ():
        if not isinstance(c, dict):
            continue
        if c.get("type") == "image":
            n += IMAGE_CHARS
        elif c.get("type") == "tool_result":
            n += _content_chars(c.get("content"))
        else:
            n += len(c.get("text") or "")
    return n


def tool_label(name, inp):
    """(label for one call, tool group) such as ('Read notes.md', 'Read')."""
    if name == "Read":
        return f"Read {Path(inp.get('file_path') or '?').name}", "Read"
    if name == "Bash":
        words = (inp.get("command") or "").split()
        return f"Bash {words[0] if words else '?'}", "Bash"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return parts[-1], parts[1] if len(parts) > 2 else name
    if name == "WebFetch":
        url = inp.get("url") or ""
        return f"WebFetch {url.split('/')[2] if url.count('/') >= 2 else url}", name
    return name, name


def _tokens(u):
    created = u.get("cache_creation_input_tokens") or 0
    split = u.get("cache_creation") or {}
    w1h = split.get("ephemeral_1h_input_tokens") or 0
    return {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
            "cache_write_5m": split.get("ephemeral_5m_input_tokens", created - w1h) or 0,
            "cache_write_1h": w1h, "cache_read": u.get("cache_read_input_tokens") or 0}


def parse_file(path, seen):
    """(threads, title, project) of one transcript.

    seen: request keys already taken by earlier files, since a resumed session can copy
    earlier messages into its own transcript.
    """
    threads, calls, tool_uses = {}, {}, {}
    pending = {}  # sidechain flag -> Call-to-be fields gathered since the last request
    title = project = None
    with open(path, "rb") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            kind = d.get("type")
            if kind == "ai-title":
                title = d.get("aiTitle") or title
                continue
            project = project or d.get("cwd")
            side = bool(d.get("isSidechain"))
            p = pending.setdefault(side, Call(0, None, None, None))
            if kind == "system" and d.get("subtype") == "compact_boundary":
                p.compacted = True
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if kind == "user":
                for c in content if isinstance(content, list) else ():
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        label, tool, fpath = tool_uses.get(c.get("tool_use_id"), ("?", "?", None))
                        p.results.append((label, tool, _content_chars(c.get("content")), fpath))
                    else:
                        p.other_chars += _content_chars([c])
                if isinstance(content, str):
                    p.other_chars += len(content)
                continue
            if kind != "assistant":
                continue
            for c in content if isinstance(content, list) else ():
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                    label, tool = tool_label(c.get("name") or "?", inp)
                    tool_uses[c.get("id")] = (label, tool, inp.get("file_path")
                                              if c.get("name") == "Read" else None)
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            key = f"{msg.get('id')}:{d.get('requestId')}"
            if key in calls:  # a later line of the same streamed response
                calls[key].tokens = _tokens(u)
                continue
            if key in seen:
                continue
            try:
                ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError, AttributeError):
                continue
            seen.add(key)
            call = pending.pop(side)
            call.ts, call.model, call.speed, call.tokens = ts, msg.get("model"), u.get("speed"), _tokens(u)
            calls[key] = call
            threads.setdefault(side, Thread(d.get("sessionId") or Path(path).stem)).calls.append(call)
    return list(threads.values()), title, project


def load_threads(root=None, since=0):
    """Threads from every transcript modified since the given time, oldest file first."""
    root = root or paths.projects_dir()
    files = []
    for path in root.glob("**/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since:
            files.append((mtime, path))
    seen, threads, titles, projects = set(), [], {}, {}
    for _, path in sorted(files):
        try:
            found, title, project = parse_file(path, seen)
        except OSError:
            continue
        for t in found:
            threads.append(t)
            if title and not titles.get(t.session):
                titles[t.session] = title
            if project and not projects.get(t.session):
                projects[t.session] = project
    return threads, titles, projects


class _Session:
    def __init__(self, sid):
        self.id = sid
        self.first = self.last = None
        self.requests = self.compactions = self.expired_count = 0
        self.cost = self.expired = self.long = 0.0
        self.peak = 0


def _rates(prices, call, unknown):
    r = pricing.rates(prices, call.model, call.speed)
    if r is None:
        if any(call.tokens.values()):  # skip zero-token placeholders such as "<synthetic>"
            unknown.add(pricing.normalize_model(call.model))
        return dict.fromkeys(pricing.FIELDS, 0.0)
    return r


def analyze(threads, prices, since):
    """Attribute the spend of requests made since `since` to causes; returns a report dict."""
    unknown = set()
    sessions = {}
    outputs = []  # every tool output: dict with label, tool, session, path, tokens, turns, cost
    split = dict.fromkeys(("read", "write", "output", "input"), 0.0)
    expired = {"cost": 0.0, "count": 0, "tokens": 0}
    long_cost, long_requests, requests = 0.0, 0, 0

    for thread in threads:
        s = sessions.get(thread.session) or sessions.setdefault(thread.session, _Session(thread.session))
        carried, prev = [], None
        for call in thread.calls:
            r = _rates(prices, call, unknown)
            t = call.tokens
            written = t["cache_write_5m"] + t["cache_write_1h"]
            write_cost = t["cache_write_5m"] * r["cache_write_5m"] + t["cache_write_1h"] * r["cache_write_1h"]
            read_cost = t["cache_read"] * r["cache_read"]
            if call.compacted:
                carried = []
                if call.ts >= since:
                    s.compactions += 1
            if prev is None:
                is_expired = written >= RESUMED_MIN_TOKENS and t["cache_read"] < written
            else:
                lifetime = 3600 if t["cache_write_1h"] or prev.tokens["cache_write_1h"] else 300
                is_expired = (call.ts - prev.ts > lifetime and written >= EXPIRED_MIN_TOKENS
                              and t["cache_read"] < written)

            # Size each new tool output by how much the context grew, split by content size;
            # fall back to a character estimate when growth can't be measured.
            new = []
            if call.results:
                chars = sum(res[2] for res in call.results) + call.other_chars
                growth = call.ctx - prev.ctx - prev.tokens["output"] if prev and not call.compacted else 0
                for label, tool, n, fpath in call.results:
                    size = growth * n / chars if growth > 0 and chars else n / CHARS_PER_TOKEN
                    new.append({"label": label, "tool": tool, "session": thread.session,
                                "path": fpath, "ts": call.ts, "tokens": size, "turns": 0,
                                "cost": 0.0})
                outputs.extend(new)

            if call.ts >= since:
                requests += 1
                c = write_cost + read_cost + t["output"] * r["output"] + t["input"] * r["input"]
                split["read"] += read_cost
                split["write"] += write_cost
                split["output"] += t["output"] * r["output"]
                split["input"] += t["input"] * r["input"]
                s.requests += 1
                s.cost += c
                s.first = s.first or call.ts
                s.last = call.ts
                s.peak = max(s.peak, call.ctx)
                if is_expired:
                    expired["cost"] += write_cost
                    expired["count"] += 1
                    expired["tokens"] += written
                    s.expired += write_cost
                    s.expired_count += 1
                else:
                    # never attribute more than the request actually read or wrote
                    held = sum(o["tokens"] for o in carried)
                    scale = min(1.0, t["cache_read"] / held) if held else 0
                    for o in carried:
                        o["cost"] += o["tokens"] * scale * r["cache_read"]
                        o["turns"] += 1
                    added = sum(o["tokens"] for o in new)
                    scale = min(1.0, written / added) if added else 0
                    per_token = write_cost / written if written else 0
                    for o in new:
                        o["cost"] += o["tokens"] * scale * per_token
                if call.ctx > LONG_CONTEXT:
                    part = read_cost * (call.ctx - LONG_CONTEXT) / call.ctx
                    long_cost += part
                    long_requests += 1
                    s.long += part
            carried.extend(new)
            prev = call

    total = sum(split.values())
    tools = defaultdict(float)
    for o in outputs:
        tools[o["tool"]] += o["cost"]
    tool_cost = sum(tools.values())

    reads = defaultdict(list)
    for o in outputs:
        if o["path"]:
            reads[(o["session"], o["path"])].append(o)
    repeats = []
    for (sid, fpath), found in reads.items():
        if len(found) >= REPEAT_MIN:
            found.sort(key=lambda o: o["ts"])
            repeats.append({"session": sid, "path": fpath, "reads": len(found),
                            "cost": sum(o["cost"] for o in found[1:])})
    repeats.sort(key=lambda x: -x["cost"])

    active = [s for s in sessions.values() if s.requests]
    return {
        "since": since,
        "sessions": len(active),
        "requests": requests,
        "cost": total,
        "split": split,
        "unknown_models": sorted(m for m in unknown if m),
        "causes": {
            "expired": {**expired, "share": _share(expired["cost"], total)},
            "long": {"cost": long_cost, "share": _share(long_cost, total),
                     "requests": long_requests, "threshold": LONG_CONTEXT},
            "tools": {"cost": tool_cost, "share": _share(tool_cost, total),
                      "by_tool": [{"tool": k, "cost": v, "share": _share(v, tool_cost)}
                                  for k, v in sorted(tools.items(), key=lambda kv: -kv[1]) if v],
                      "top": [_output(o) for o in sorted(outputs, key=lambda o: -o["cost"])[:TOP]
                              if o["cost"]]},
            "repeats": {"cost": sum(x["cost"] for x in repeats),
                        "share": _share(sum(x["cost"] for x in repeats), total),
                        "files": repeats},
        },
        "session_list": [_session(s) for s in sorted(active, key=lambda s: -s.cost)],
        "_outputs": outputs,
    }


def _share(part, total):
    return part / total if total else 0.0


def _output(o):
    return {"label": o["label"], "tool": o["tool"], "session": o["session"], "tokens": round(o["tokens"]),
            "turns": o["turns"], "cost": o["cost"]}


def _session(s):
    return {"id": s.id, "first": s.first, "last": s.last, "requests": s.requests,
            "cost": s.cost, "peak_context": s.peak, "compactions": s.compactions,
            "expired": {"count": s.expired_count, "cost": s.expired}, "long": s.long}


# --- output ---

def _usd(x):
    return f"≈{fmt_usd(x)}".rjust(9)


def _label(output, show_titles):
    """Tool output label; without titles, only the tool, since names can be revealing."""
    return output["label"] if show_titles else output["tool"]


def _file(path, show_titles):
    return Path(path).name if show_titles else "a file"


def _pct(x):
    return f"{x * 100:.0f}%"


def _title(sid, titles, show_titles):
    short = sid[:8]
    title = titles.get(sid) if show_titles else None
    if not title:
        return f"[{short}]"
    title = title if len(title) <= 30 else title[:29] + "…"
    return f'"{title}" [{short}]'


def text_report(report, days, titles, show_titles=True):
    if not report["requests"]:
        return f"No Claude Code usage in the last {days} days."
    total = report["cost"]
    sp = report["split"]
    lines = [
        f"Last {days} days · {report['sessions']} sessions · {report['requests']:,} requests"
        f" · ≈{fmt_usd(total)} API-equivalent",
        f"  cache reads {_pct(_share(sp['read'], total))}"
        f"   cache writes {_pct(_share(sp['write'], total))}"
        f"   output {_pct(_share(sp['output'], total))}",
    ]
    if report["unknown_models"]:
        lines.append(f"  (no prices for {', '.join(report['unknown_models'])}; counted as $0)")

    c = report["causes"]
    blocks = []
    e = c["expired"]
    if e["count"]:
        blocks.append((e["cost"], "Resumed after the cache expired", e["share"], [
            f"{e['count']} requests after an idle gap wrote the context again"
            f" (avg {fmt_tokens(e['tokens'] / e['count'])} tokens).",
        ], ADVICE["expired"]))
    lc = c["long"]
    if lc["requests"]:
        biggest = sorted(report["session_list"], key=lambda s: -s["peak_context"])[:2]
        blocks.append((lc["cost"], "Long context (overlaps the others)", lc["share"], [
            f"{_pct(_share(lc['requests'], report['requests']))} of requests ran above"
            f" {fmt_tokens(lc['threshold'])} tokens of context.",
            "Largest: " + ", ".join(f"{_title(s['id'], titles, show_titles)}"
                                    f" ({fmt_tokens(s['peak_context'])})" for s in biggest),
        ], ADVICE["long"]))
    tc = c["tools"]
    if tc["cost"]:
        body = [" · ".join(f"{x['tool']} {_pct(x['share'])}" for x in tc["by_tool"][:4])]
        if tc["top"]:
            o = tc["top"][0]
            body.append(f"Costliest: {_label(o, show_titles)} ({fmt_tokens(o['tokens'])} tokens,"
                        f" carried {o['turns']} turns, ≈{fmt_usd(o['cost'])})")
        blocks.append((tc["cost"], "Tool outputs kept in context", tc["share"], body,
                       ADVICE["tools"]))
    rc = c["repeats"]
    if rc["files"]:
        blocks.append((rc["cost"], "Same file read repeatedly (part of tool outputs)",
                       rc["share"], [", ".join(
                           f"{_file(x['path'], show_titles)} ×{x['reads']}" for x in rc["files"][:3])
                           + " (each within a single session)"], ADVICE["repeats"]))

    if blocks:
        lines += ["", "Top causes"]
        width = max(len(b[1]) for b in blocks)
        for i, (cost, name, share, body, advice) in enumerate(
                sorted(blocks, key=lambda b: -b[0]), 1):
            lines.append(f" {i}. {name.ljust(width)}  {_usd(cost)}  {_pct(share):>4}")
            lines += [f"    {b}" for b in body]
            lines.append(f"    → {advice}")
    top = report["session_list"][:3]
    lines += ["", "Costliest sessions"]
    lines += [f"  {_usd(s['cost'])}  {_title(s['id'], titles, show_titles)}" for s in top]
    lines.append("Run `tallyline diagnose --session <id>` for one session's breakdown.")
    return "\n".join(lines)


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def session_report(report, sid, titles, projects, show_titles=True):
    s = next(x for x in report["session_list"] if x["id"] == sid)
    head = f"Session {_title(sid, titles, show_titles)}"
    if show_titles and projects.get(sid):
        head += f" · {projects[sid].replace(str(Path.home()), '~')}"
    lines = [head,
             f"  {_day(s['first'])} → {_day(s['last'])} · {s['requests']:,} requests"
             f" · ≈{fmt_usd(s['cost'])} · peak context {fmt_tokens(s['peak_context'])}"]
    if s["expired"]["count"]:
        lines.append(f"  Resumed after the cache expired: {s['expired']['count']}×"
                     f" ≈{fmt_usd(s['expired']['cost'])}")
    if s["long"]:
        lines.append(f"  Spent on context above {fmt_tokens(LONG_CONTEXT)}: ≈{fmt_usd(s['long'])}")
    if s["compactions"]:
        lines.append(f"  Compactions: {s['compactions']}")
    outputs = sorted((o for o in report["_outputs"] if o["session"] == sid and o["cost"]),
                     key=lambda o: -o["cost"])[:TOP]
    if outputs:
        lines.append("  Costliest tool outputs:")
        lines += [f"    {_usd(o['cost'])}  {_label(o, show_titles)} ({fmt_tokens(o['tokens'])} tokens,"
                  f" carried {o['turns']} turns)" for o in outputs]
    repeats = [x for x in report["causes"]["repeats"]["files"] if x["session"] == sid]
    if repeats:
        lines.append("  Read repeatedly: " + ", ".join(
            f"{_file(x['path'], show_titles)} ×{x['reads']}" for x in repeats))
    return "\n".join(lines)


def _rounded(value):
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def json_report(report, titles, projects, show_titles=True):
    out = _rounded({k: v for k, v in report.items() if not k.startswith("_")})
    out["since"] = datetime.fromtimestamp(report["since"]).astimezone().isoformat()
    for s in out["session_list"]:
        if show_titles:
            s["title"] = titles.get(s["id"])
            s["project"] = projects.get(s["id"])
    if not show_titles:
        for f in out["causes"]["repeats"]["files"]:
            f["path"] = None
        for o in out["causes"]["tools"]["top"]:
            o["label"] = None
    return json.dumps(out, indent=2, ensure_ascii=False)


def find_session(report, prefix):
    matches = [s["id"] for s in report["session_list"] if s["id"].startswith(prefix)]
    return matches


def run(days=30, session=None, as_json=False, show_titles=True, prices=None, now=None):
    """Text (or JSON) report for the last `days` days; raises LookupError for a bad --session."""
    since = (now or time.time()) - days * 86400
    threads, titles, projects = load_threads(since=since)
    report = analyze(threads, prices if prices is not None else pricing.load(), since)
    if session:
        matches = find_session(report, session)
        if len(matches) != 1:
            raise LookupError(f"no session matches {session!r} in the last {days} days"
                              if not matches else
                              f"{session!r} matches {len(matches)} sessions; use more characters")
        if as_json:
            report = {**report, "session_list": [s for s in report["session_list"]
                                                 if s["id"] == matches[0]]}
            report["_outputs"] = []
            return json_report(report, titles, projects, show_titles)
        return session_report(report, matches[0], titles, projects, show_titles)
    if as_json:
        return json_report(report, titles, projects, show_titles)
    return text_report(report, days, titles, show_titles)
