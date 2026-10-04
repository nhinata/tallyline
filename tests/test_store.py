from conftest import usage_line

from tallyline import paths, store


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
    store.rescan(db)
    store.ingest(db)
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
    store.rescan(db)
    store.ingest(db)
    assert db.execute("SELECT SUM(input) FROM daily").fetchone()[0] == 3
