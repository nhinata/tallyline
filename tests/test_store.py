import json

from conftest import usage_line

from tallyline import paths, store, transcript


def write(path, text, mode="a"):
    with open(path, mode) as f:
        f.write(text)


def all_rows(db):
    return db.execute("SELECT key, input, output, cache_write_5m, cache_write_1h, cache_read "
                      "FROM usage ORDER BY key").fetchall()


def test_duplicate_streaming_lines_are_counted_once(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    line = usage_line("m1", "r1", out=100)
    write(log, line + line)
    db = store.connect()
    store.ingest(db)
    assert all_rows(db) == [("m1:r1", 0, 100, 0, 0, 0)]


def test_malformed_usage_is_skipped(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    good = json.loads(usage_line("good", "r", inp=1))

    def bad(usage=None, **top):
        msg = {**good["message"], "id": "bad"}
        if usage is not None:
            msg["usage"] = usage
        return json.dumps({**good, "message": msg, **top}) + "\n"

    lines = [bad(usage) for usage in (
        "text", [], {"cache_creation": "text"}, {"input_tokens": "1"}, {"input_tokens": [1]},
        {"input_tokens": 1.5}, {"input_tokens": -1}, {"input_tokens": 10 ** 30},
        {"cache_creation_input_tokens": "1"}, {"speed": {"a": 1}},
    )]
    lines.append(bad(usage={}, message={"id": "bad", "usage": None}))
    lines.append(bad(usage={}, message={"id": "bad", "model": {"a": 1}, "usage": {}}))
    lines.append(bad(sessionId={"a": 1}))
    lines += [bad(timestamp=t) for t in ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z")]
    write(log, "".join(lines) + usage_line("good", "r", inp=1))
    db = store.connect()
    store.ingest(db)
    assert [r[0] for r in all_rows(db)] == ["good:r"]
    assert db.execute("SELECT SUM(input) FROM daily").fetchone()[0] == 1


def test_values_at_the_token_limit_can_still_be_summed(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, "".join(usage_line(f"m{i}", "r", inp=transcript.MAX_TOKENS - 1) for i in range(3)))
    db = store.connect()
    store.ingest(db)
    assert store.totals_since(db, "2000-01-01")[0][2]["input"] == 3 * (transcript.MAX_TOKENS - 1)


def test_unreadable_transcript_does_not_stop_the_others(home):
    (paths.projects_dir() / "proj" / "a.jsonl").mkdir()  # open() fails on every platform
    (paths.projects_dir() / "proj" / "a.jsonl" / "child").write_text("keeps its size above 0")
    write(paths.projects_dir() / "proj" / "b.jsonl", usage_line("m1", "r1", inp=1))
    db = store.connect()
    store.ingest(db)
    assert [r[0] for r in all_rows(db)] == ["m1:r1"]


def test_rebuild_reports_unreadable_transcripts(home):
    unreadable = paths.projects_dir() / "proj" / "a.jsonl"
    unreadable.mkdir()
    (unreadable / "child").write_text("keeps its size above 0")
    write(paths.projects_dir() / "proj" / "b.jsonl", usage_line("m1", "r1", inp=1))
    db = store.connect()
    assert [p for p, _ in store.rebuild(db)] == [unreadable]
    assert [r[0] for r in all_rows(db)] == ["m1:r1"]


def test_transcript_deleted_before_it_is_opened_is_not_reported(home, monkeypatch):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1", inp=1))
    real_open = open

    def deleted_first(path, *args, **kwargs):
        if str(path) == str(log.absolute()):
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", deleted_first)
    assert store.rebuild(store.connect()) == []


def test_rebuild_changes_nothing_while_another_process_holds_the_lock(home):
    write(paths.projects_dir() / "proj" / "s.jsonl", usage_line("m1", "r1", inp=1))
    db = store.connect()
    store.ingest(db)
    other = store.connect()
    other.isolation_level = None
    other.execute("BEGIN IMMEDIATE")
    try:
        assert store.rebuild(db, wait_ms=10) is None
    finally:
        other.execute("ROLLBACK")
    assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_only_appended_bytes_are_parsed_and_partial_lines_wait(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1", inp=1))
    db = store.connect()
    store.ingest(db)
    second = usage_line("m2", "r2", inp=2)
    write(log, second[:20])  # line still being written
    store.ingest(db)
    assert len(all_rows(db)) == 1
    write(log, second[20:])
    store.ingest(db)
    assert [r[0] for r in all_rows(db)] == ["m1:r1", "m2:r2"]


def test_truncated_file_is_rescanned(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1") + usage_line("m2", "r2"))
    db = store.connect()
    store.ingest(db)
    write(log, usage_line("m3", "r3"), mode="w")
    store.ingest(db)
    assert [r[0] for r in all_rows(db)] == ["m1:r1", "m2:r2", "m3:r3"]


def test_subagent_transcripts_in_subdirectories_are_included(home):
    sub = paths.projects_dir() / "proj" / "session" / "subagents"
    sub.mkdir(parents=True)
    write(sub / "a.jsonl", usage_line("m1", "r1"))
    db = store.connect()
    store.ingest(db)
    assert len(all_rows(db)) == 1


def test_cache_write_split_and_fallback_without_breakdown():
    row = store.parse_line(usage_line("m", "r", w5m=10, w1h=30).encode())
    assert row[7:9] == (10, 30)
    legacy = b'{"timestamp":"2026-10-03T00:00:00Z","requestId":"r","message":{"id":"m",' \
             b'"usage":{"cache_creation_input_tokens":40}}}'
    assert store.parse_line(legacy)[7:9] == (40, 0)


def test_non_usage_and_malformed_lines_are_ignored():
    assert store.parse_line(b'{"type":"user"}') is None
    assert store.parse_line(b'{"usage": broken') is None


def test_totals_since_filters_by_time_and_groups_by_model(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("old", "r", ts="2026-01-01T00:00:00Z", inp=5)
          + usage_line("a", "r", inp=1, model="claude-sonnet-5")
          + usage_line("b", "r", inp=2, model="claude-sonnet-5")
          + usage_line("c", "r", inp=4, model="claude-opus-5-5"))
    db = store.connect()
    store.ingest(db)
    got = {m: t["input"] for m, _, t in store.totals_since(db, "2026-10-01")}
    assert got == {"claude-sonnet-5": 3, "claude-opus-5-5": 4}


def test_session_totals_include_subagents_and_exclude_other_sessions(home):
    proj = paths.projects_dir() / "proj"
    (proj / "s1" / "subagents").mkdir(parents=True)
    write(proj / "s1.jsonl", usage_line("a", "r", inp=1, session="s1"))
    write(proj / "s1" / "subagents" / "x.jsonl", usage_line("b", "r", inp=2, session="s1"))
    write(proj / "s2.jsonl", usage_line("c", "r", inp=4, session="s2"))
    db = store.connect()
    store.ingest(db)
    assert sum(t["input"] for _, _, t in store.totals_for_session(db, "s1")) == 3


def test_history_survives_transcript_deletion_and_rebuild(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1", inp=1))
    db = store.connect()
    store.ingest(db)
    log.unlink()  # Claude Code's cleanupPeriodDays removed the transcript
    assert store.rebuild(db) == []
    assert [r[0] for r in all_rows(db)] == ["m1:r1"]


def test_offsets_of_deleted_transcripts_are_forgotten_but_rows_kept(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1"))
    db = store.connect()
    elsewhere = str(home / "unmounted" / "x.jsonl")  # another root, not available now
    db.execute("INSERT INTO files VALUES (?, 10)", (elsewhere,))
    store.ingest(db)
    log.unlink()  # nothing else changes, and the offset is still dropped
    store.ingest(db)
    assert [p for p, in db.execute("SELECT path FROM files")] == [elsewhere]
    assert [r[0] for r in all_rows(db)] == ["m1:r1"]


def test_v1_database_is_migrated_in_place(home):
    import sqlite3
    path = paths.db_path()
    path.parent.mkdir(parents=True)
    old = sqlite3.connect(str(path))
    old.executescript(store.MIGRATIONS[1] + "PRAGMA user_version = 1;")
    old.execute("INSERT INTO usage VALUES ('old:r', 1767268800, 'claude-sonnet-5', 'standard', 7, 0, 0, 0, 0)")
    old.commit()
    old.close()
    db = store.connect()
    assert db.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    assert db.execute("SELECT key, input, session FROM usage").fetchall() == [("old:r", 7, None)]
    assert db.execute("SELECT day, input FROM daily").fetchall() == [("2026-01-01", 7)]


def test_rescan_refreshes_rows_but_keeps_first_session(home):
    proj = paths.projects_dir() / "proj"
    write(proj / "a.jsonl", usage_line("m", "r", inp=1, session="first"))
    db = store.connect()
    store.ingest(db)
    write(proj / "b.jsonl", usage_line("m", "r", inp=1, session="resumed"))
    store.ingest(db)
    assert db.execute("SELECT session FROM usage").fetchall() == [("first",)]


def test_daily_rollup_matches_raw_rows_after_upserts(home):
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("a", "r", inp=1) + usage_line("b", "r", inp=2) + usage_line("a", "r", inp=1))
    db = store.connect()
    store.ingest(db)
    store.rebuild(db)
    assert db.execute("SELECT SUM(input) FROM daily").fetchone()[0] == 3


def test_ingest_skips_without_waiting_while_another_process_ingests(home):
    path = paths.projects_dir() / "proj" / "s1.jsonl"
    write(path, usage_line("m1", "r1", inp=5))
    other = store.connect()
    other.isolation_level = None
    other.execute("BEGIN IMMEDIATE")  # another render holding the write lock
    db = store.connect()
    assert store.ingest(db) is False
    assert all_rows(db) == []
    other.execute("ROLLBACK")
    assert store.ingest(db) is True
    assert len(all_rows(db)) == 1


def test_database_uses_wal_so_reads_do_not_wait_for_writes(home):
    db = store.connect()
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    writer = store.connect()
    writer.isolation_level = None
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO files VALUES ('x', 1)")
    assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0  # not blocked
    writer.execute("COMMIT")


def test_unreadable_transcript_is_not_taken_for_deleted(monkeypatch):
    def denied(path):
        raise PermissionError(path)
    monkeypatch.setattr(store.os, "stat", denied)
    assert not store._missing("/x/s.jsonl")


def test_relative_config_dir_is_stored_as_absolute_paths(home, monkeypatch):
    monkeypatch.chdir(home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "claude")
    log = paths.projects_dir() / "proj" / "s.jsonl"
    write(log, usage_line("m1", "r1"))
    db = store.connect()
    store.ingest(db)
    assert db.execute("SELECT path FROM files").fetchall() == [(str(home / log),)]
