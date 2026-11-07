import copy
import json
import os

import pytest
from conftest import usage_line

from tallyline import cli, diagnose, paths, sessions, store, transcript

NOW = 1_791_000_000  # 2026-10-03
BAD_VALUES = (None, 1, -1, 1.5, "x", [], {"a": 1}, 10 ** 30, "9999-12-31T23:59:59Z",
              "a\ud83d")


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def transcript_lines():
    """One line of each kind the commands read, with every optional field filled in."""
    def request(n, ts, content=()):
        line = json.loads(usage_line(f"m{n}", f"r{n}", ts=iso(ts), inp=1, out=2, w1h=30_000,
                                     read=300_000))
        line["cwd"] = "/p"
        line["message"]["content"] = list(content)
        return line

    tools = [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp} for i, (name, inp) in
             enumerate((("Read", {"file_path": "/a/b.md"}), ("Bash", {"command": "ls -l"}),
                        ("WebFetch", {"url": "https://x.y/z"}), ("mcp__s__t", {})))]
    results = [{"type": "tool_result", "tool_use_id": "t0", "content": "x" * 5000},
               {"type": "tool_result", "tool_use_id": "t1",
                "content": [{"type": "text", "text": "y" * 5000}, {"type": "image"}]},
               {"type": "text", "text": "more"}]
    return [
        {"type": "ai-title", "aiTitle": "title"},
        request(1, NOW - 90_000, tools),
        {"type": "user", "timestamp": iso(NOW - 89_990), "message": {"content": results}},
        request(2, NOW - 80_000),
        {"type": "system", "subtype": "compact_boundary", "timestamp": iso(NOW - 70_000)},
        {"type": "user", "timestamp": iso(NOW - 60_000), "message": {"content": "plain"}},
        request(3, NOW - 3000),
    ]


def field_paths(value, path=()):
    yield path
    items = value.items() if isinstance(value, dict) else enumerate(value) \
        if isinstance(value, list) else ()
    for key, child in items:
        yield from field_paths(child, path + (key,))


def with_value(value, path, new):
    if not path:
        return new
    value = copy.deepcopy(value)
    parent = value
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = new
    return value


def mutations():
    lines = transcript_lines()
    for i, line in enumerate(lines):
        for path in field_paths(line):
            for bad in BAD_VALUES:
                yield lines[:i] + [with_value(line, path, bad)] + lines[i + 1:]


def write(lines):
    log = paths.projects_dir() / "proj" / "s1.jsonl"
    log.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return log


def test_commands_survive_a_wrong_value_in_any_field(home, monkeypatch, capsys):
    monkeypatch.setattr("time.time", lambda: NOW)
    monkeypatch.setattr(sessions, "_process_table", dict)
    monkeypatch.setattr(sessions, "_tmux_panes", dict)
    sessions_dir = paths.claude_dir() / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "1.json").write_text(json.dumps(
        {"pid": os.getpid(), "sessionId": "s1", "status": "idle"}))
    for lines in mutations():
        write(lines)
        for argv in (["diagnose"], ["diagnose", "--json"], ["sessions"], ["sessions", "--json"]):
            cli.main(argv)
    capsys.readouterr()


@pytest.mark.parametrize("line", [b"[" * 200_000, b"[1]", b"1", b"null", b"not json", b"\xff"])
def test_load_line_rejects_anything_but_an_object(line):
    assert transcript.load_line(line) is None


def test_deeply_nested_line_does_not_stop_the_store():
    assert store.parse_line(b"[" * 200_000 + b'"usage"') is None


def test_diagnose_counts_the_same_requests_as_the_store(home):
    good = json.loads(usage_line("good", "r", inp=7))
    bad = [with_value(good, ("message", "usage", "input_tokens"), "1"),
           with_value(good, ("message", "model"), {"a": 1}),
           with_value(good, ("timestamp",), "9999-12-31T23:59:59Z")]
    bad = [with_value(b, ("message", "id"), f"bad{i}") for i, b in enumerate(bad)]
    write(bad + [good])

    db = store.connect()
    store.ingest(db)
    threads, _, _ = diagnose.load_threads()

    assert store.totals_since(db, "2000-01-01")[0][2]["input"] == 7
    assert [c.tokens["input"] for t in threads for c in t.calls] == [7]


def test_a_later_bad_line_keeps_the_tokens_of_the_streamed_response(home):
    first = json.loads(usage_line("m", "r", out=5))
    write([first, with_value(first, ("message", "usage", "output_tokens"), "x")])
    threads, _, _ = diagnose.load_threads()
    assert threads[0].calls[0].tokens["output"] == 5


def test_store_skips_text_that_cant_be_encoded(home):
    good = json.loads(usage_line("good", "r", inp=7))
    bad = [with_value(good, ("message", "model"), "a\ud83d"),
           with_value(good, ("sessionId",), "a\ud83d"),
           with_value(good, ("message", "usage", "speed"), "a\ud83d"),
           with_value(good, ("requestId",), "a\ud83d")]
    write([with_value(b, ("message", "id"), f"bad{i}") for i, b in enumerate(bad)] + [good])

    db = store.connect()
    store.ingest(db)

    assert store.totals_since(db, "2000-01-01")[0][2]["input"] == 7


def test_deeply_nested_content_is_counted_without_recursion():
    content = "x" * 10
    for _ in range(100_000):
        content = [{"type": "tool_result", "content": content}]
    assert diagnose._content_chars(content) == 10


def test_tool_result_with_a_bad_id_is_not_matched_to_a_tool_use_without_id(home):
    lines = transcript_lines()
    del lines[1]["message"]["content"][0]["id"]
    lines[2]["message"]["content"][0]["tool_use_id"] = 1
    write(lines)
    threads, _, _ = diagnose.load_threads()
    labels = [r[0] for t in threads for c in t.calls for r in c.results]
    assert "Read b.md" not in labels
    assert "?" in labels


def test_sessions_context_is_the_reported_total_not_the_split(home):
    log = write([
        json.loads(usage_line("m1", "r1", read=1000)),
        with_value(json.loads(usage_line("m2", "r2", w1h=521)),
                   ("message", "usage", "cache_creation_input_tokens"), 0),
    ])
    assert sessions.read_transcript(log)["context"] == 1000


def test_sessions_keeps_the_cache_lifetime_when_a_later_line_has_no_split(home):
    later = json.loads(usage_line("m2", "r2", w5m=100))
    del later["message"]["usage"]["cache_creation"]
    log = write([json.loads(usage_line("m1", "r1", w1h=100)), later])
    assert sessions.read_transcript(log)["ttl"] == "1h"


def test_text_accepts_only_non_empty_strings():
    assert transcript.text({"k": "v"}, "k") == "v"
    assert [transcript.text({"k": v}, "k") for v in ("", 1, [], None)] == [None] * 4
    assert transcript.text("not a dict", "k") is None


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "x", 1, None])
def test_parse_timestamp_rejects_invalid_and_out_of_range_values(value):
    assert transcript.parse_timestamp(value) is None
