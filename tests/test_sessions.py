import json
import os
import subprocess
import sys

import pytest
from conftest import usage_line

from tallyline import cli, paths, sessions

PRICES = {"m": {"input": 1.0, "cache_write_5m": 2.0, "cache_write_1h": 4.0,
                "cache_read": 0.5, "output": 10.0}}
NOW = 1_791_000_000  # 2026-10-03
ME, PARENT = os.getpid(), os.getppid()


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def session_file(pid, sid, **extra):
    d = paths.claude_dir() / "sessions"
    d.mkdir(exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "sessionId": sid, "cwd": "/secret/project", "status": "idle", **extra}))


def transcript(sid, title=None, calls=()):
    """calls: [(ts, read, w1h, w5m)]"""
    lines = [json.dumps({"type": "ai-title", "aiTitle": title, "sessionId": sid}) + "\n"
             ] if title else []
    for i, (ts, read, w1h, w5m) in enumerate(calls):
        lines.append(usage_line(f"{sid}-m{i}", f"r{i}", ts=iso(ts), model="m", session=sid,
                                read=read, w1h=w1h, w5m=w5m))
    (paths.projects_dir() / "proj" / f"{sid}.jsonl").write_text("".join(lines))


def collect(table=None, panes=None):
    return sessions.collect(PRICES, now=NOW, table=table or {}, panes=panes or {})


def test_expired_session_shows_resume_cost_at_its_cache_write_price(home):
    session_file(ME, "a1")
    transcript("a1", "Fix login", [(NOW - 3 * 3600, 90_000, 10_000, 0)])
    session_file(PARENT, "b2")
    transcript("b2", "Quick question", [(NOW - 600, 0, 0, 20_000)])  # 5-minute cache
    a, b = sorted(collect(), key=lambda s: s["id"])
    assert (a["title"], a["context"], a["cache"], a["cache_lifetime"]) == (
        "Fix login", 100_000, "expired", "1h")
    assert a["resume_cost"] == pytest.approx(100_000 * 4.0 / 1e6)
    assert b["cache"] == "expired" and b["resume_cost"] == pytest.approx(20_000 * 2.0 / 1e6)


def test_recent_and_busy_sessions_are_warm(home):
    session_file(ME, "a1")
    transcript("a1", None, [(NOW - 600, 50_000, 1_000, 0)])  # 10 minutes, 1-hour cache
    session_file(PARENT, "b2", status="busy")
    transcript("b2", None, [(NOW - 7200, 50_000, 1_000, 0)])
    assert {s["cache"] for s in collect()} == {"warm"}
    assert all(s["resume_cost"] is None for s in collect())


def test_dead_and_unreadable_session_files_are_skipped(home):
    session_file(dead_pid(), "gone")
    (paths.claude_dir() / "sessions" / "123.json").write_text("{not json")
    session_file(ME, "a1")
    assert [s["id"] for s in collect()] == ["a1"]


def test_location_this_session_and_background_continuation(home):
    session_file(ME, "fg", tmux="work:@4.%4", parkedJobId="job9")
    session_file(PARENT, "bg", kind="bg", jobId="job9")
    for sid in ("fg", "bg"):
        transcript(sid, None, [(NOW - 60, 1_000, 0, 0)])
    out = {s["id"]: s for s in collect(table={ME: (PARENT, "ttys001")},
                                       panes={"@4.%4": ("work:2", "api")})}
    assert out["fg"]["where"] == {"kind": "tmux", "target": "work:2", "name": "api"}
    assert out["fg"]["this_session"] and not out["bg"]["this_session"]
    assert out["bg"]["where"]["kind"] == "background"
    assert out["fg"]["continued_as"] == out["bg"]["n"]


def test_title_is_read_from_anywhere_in_a_large_transcript(home, monkeypatch):
    monkeypatch.setattr(sessions, "TAIL_BYTES", 200)
    session_file(ME, "a1")
    transcript("a1", "Early title", [(NOW - 7200 + i, 1_000 * i, 0, 10) for i in range(1, 20)])
    s = collect()[0]
    assert s["title"] == "Early title" and s["context"] == 19_010


def test_text_report_and_hiding_titles(home):
    session_file(ME, "a1b2c3d4e5", tmux="work:@1.%1")
    transcript("a1b2c3d4e5", "Secret plan", [(NOW - 2 * 86400, 200_000, 1_000, 0)])
    panes = {"@1.%1": ("work:1", "private-window")}
    shown = sessions.text_report(collect(panes=panes))
    assert "1 open Claude Code session · 1 with an expired cache" in shown
    assert "Secret plan" in shown and "tmux work:1 private-window" in shown
    assert "2.0d ago" in shown and "201k" in shown and "≈$0.80" in shown
    hidden = sessions.text_report(collect(panes=panes), show_titles=False)
    assert "Secret" not in hidden and "private-window" not in hidden and "[a1b2c3d4]" in hidden
    as_json = sessions.json_report(collect(panes=panes), show_titles=False)
    assert "Secret" not in as_json and "/secret/project" not in as_json


def test_sort_by_resume_cost(home):
    session_file(ME, "small")
    transcript("small", None, [(NOW - 7200, 10_000, 1_000, 0)])
    session_file(PARENT, "big")
    transcript("big", None, [(NOW - 86400, 300_000, 1_000, 0)])
    out = sessions.run(sort="resume", prices=PRICES, now=NOW, table={}, panes={})
    assert out.index("[big]") < out.index("[small]")


def test_cli_with_no_sessions(home, capsys):
    cli.main(["sessions"])
    assert "No open Claude Code sessions." in capsys.readouterr().out
