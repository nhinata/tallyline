import json

import pytest
from conftest import usage_line

from tallyline import cli, diagnose, paths

# USD per million tokens, chosen so expected costs are easy to compute by hand
PRICES = {"m": {"input": 1.0, "cache_write_5m": 2.0, "cache_write_1h": 4.0,
                "cache_read": 0.5, "output": 10.0}}
NOW = 1_791_000_000  # 2026-10-03
DAY = 86400


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class Log:
    """Builds one transcript file line by line."""

    def __init__(self, name="s1", session="s1"):
        self.name = name
        self.path = paths.projects_dir() / "proj" / f"{name}.jsonl"
        self.session = session
        self.n = 0

    def _write(self, obj):
        with open(self.path, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def call(self, ts, read=0, w1h=0, w5m=0, out=0, tools=()):
        """One request. tools: [(id, name, input)] tool_use blocks in its response."""
        self.n += 1
        line = json.loads(usage_line(f"{self.name}-m{self.n}", f"r{self.n}", ts=iso(ts), model="m",
                                     session=self.session, out=out, w5m=w5m, w1h=w1h,
                                     read=read))
        line["message"]["content"] = [{"type": "tool_use", "id": i, "name": n, "input": inp}
                                      for i, n, inp in tools]
        self._write(line)

    def result(self, tool_id, chars):
        self._write({"type": "user", "sessionId": self.session, "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                         "content": "x" * chars}]}})

    def compact(self):
        self._write({"type": "system", "subtype": "compact_boundary", "sessionId": self.session})

    def title(self, text):
        self._write({"type": "ai-title", "aiTitle": text, "sessionId": self.session})


def report(days=30):
    since = NOW - days * DAY
    threads, _, _ = diagnose.load_threads(since=0)
    return diagnose.analyze(threads, PRICES, since)


def test_rewrite_after_idle_gap_counts_as_expired(home):
    log = Log()
    log.call(NOW - 5 * 3600, w1h=20_000)
    log.call(NOW - 5 * 3600 + 600, read=20_000, w1h=1_000)  # 10 minutes later: cache hit
    log.call(NOW - 2 * 3600, w1h=21_000)                    # 3 hours later: rewritten
    e = report()["causes"]["expired"]
    assert e["count"] == 1
    assert e["tokens"] == 21_000
    assert e["cost"] == pytest.approx(21_000 * 4.0 / 1e6)


def test_short_gap_with_5m_cache_uses_5m_lifetime(home):
    log = Log()
    log.call(NOW - 3600, w5m=20_000)
    log.call(NOW - 3600 + 400, w5m=20_000)  # over 5 minutes later
    assert report()["causes"]["expired"]["count"] == 1


def test_first_request_of_resumed_transcript_is_expired_but_fresh_start_is_not(home):
    Log("fresh", "a").call(NOW - 3600, w1h=30_000)
    Log("resumed", "b").call(NOW - 3600, w1h=300_000)
    e = report()["causes"]["expired"]
    assert e["count"] == 1 and e["tokens"] == 300_000


def test_tool_output_is_sized_by_context_growth_and_carried_until_compaction(home):
    log = Log()
    log.call(NOW - 600, w1h=20_000, tools=[("t1", "Read", {"file_path": "/x/notes.md"})])
    log.result("t1", 3_000)
    log.call(NOW - 500, read=20_000, w1h=10_000)  # context grew by 10k: the tool output
    log.call(NOW - 400, read=30_000)              # carried once
    log.compact()
    log.call(NOW - 300, w1h=5_000)                # gone after compaction
    log.call(NOW - 200, read=5_000)
    tools = report()["causes"]["tools"]
    top = tools["top"][0]
    assert top["label"] == "Read notes.md"
    assert top["tokens"] == 10_000
    assert top["turns"] == 1
    assert top["cost"] == pytest.approx((10_000 * 4.0 + 10_000 * 0.5) / 1e6)
    assert tools["by_tool"][0]["tool"] == "Read"


def test_attribution_never_exceeds_what_the_request_read(home):
    log = Log()
    log.call(NOW - 600, w1h=1_000, tools=[("t1", "Bash", {"command": "cat big"})])
    log.result("t1", 300_000)  # no measurable growth below, so sized by characters
    log.call(NOW - 500, read=500)
    log.call(NOW - 400, read=500)
    top = report()["causes"]["tools"]["top"][0]
    assert top["label"] == "Bash cat"
    assert top["cost"] <= 500 * 0.5 / 1e6 + 1e-12


def test_same_file_read_repeatedly_in_one_session(home):
    log = Log()
    for i in range(3):
        log.call(NOW - 600 + i * 10, read=10_000 * i, w1h=10_000,
                 tools=[(f"t{i}", "Read", {"file_path": "/x/a.md"})])
        log.result(f"t{i}", 1_000)
    log.call(NOW - 500, read=40_000)
    files = report()["causes"]["repeats"]["files"]
    assert [(f["path"], f["reads"]) for f in files] == [("/x/a.md", 3)]


def test_long_context_counts_the_part_above_the_threshold(home):
    Log().call(NOW - 600, read=300_000)
    lc = report()["causes"]["long"]
    assert lc["requests"] == 1
    assert lc["cost"] == pytest.approx(300_000 * 0.5 / 1e6 * (100_000 / 300_000))


def test_only_requests_in_the_period_are_counted(home):
    log = Log()
    log.call(NOW - 40 * DAY, w1h=10_000)
    log.call(NOW - DAY, read=10_000)
    r = report(days=30)
    assert r["requests"] == 1
    assert r["cost"] == pytest.approx(10_000 * 0.5 / 1e6)


def test_streamed_duplicates_and_copied_requests_count_once(home):
    log = Log()
    log.call(NOW - 600, w1h=10_000)
    with open(log.path) as f:
        line = f.read()
    with open(log.path, "a") as f:
        f.write(line)  # same response written again while streaming
    (paths.projects_dir() / "proj" / "copy.jsonl").write_text(line)  # resumed copy
    assert report()["requests"] == 1


def test_text_report_and_hiding_titles(home):
    log = Log()
    log.title("secret project")
    log.call(NOW - 5 * 3600, w1h=20_000, tools=[("t1", "Read", {"file_path": "/x/private.md"})])
    log.result("t1", 1_000)
    log.call(NOW - 3600, w1h=300_000)
    threads, titles, projects = diagnose.load_threads(since=0)
    r = diagnose.analyze(threads, PRICES, NOW - 30 * DAY)
    shown = diagnose.text_report(r, 30, titles)
    assert "Resumed after the cache expired" in shown and "secret project" in shown
    hidden = diagnose.text_report(r, 30, titles, show_titles=False)
    assert "secret" not in hidden and "private.md" not in hidden
    as_json = json.loads(diagnose.json_report(r, titles, projects, show_titles=False))
    assert "secret" not in json.dumps(as_json) and "private.md" not in json.dumps(as_json)


def test_session_lookup_by_prefix(home):
    Log("a", "abc111").call(NOW - 600, w1h=10_000)
    Log("b", "abc222").call(NOW - 600, w1h=10_000)
    out = diagnose.run(session="abc1", prices=PRICES, now=NOW)
    assert out.startswith("Session [abc111]")
    with pytest.raises(LookupError):
        diagnose.run(session="abc", prices=PRICES, now=NOW)
    with pytest.raises(LookupError):
        diagnose.run(session="zzz", prices=PRICES, now=NOW)


def test_cli_with_no_usage(home, capsys):
    cli.main(["diagnose", "--days", "7"])
    assert "No Claude Code usage in the last 7 days" in capsys.readouterr().out
