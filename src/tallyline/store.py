"""Incremental usage store.

Claude Code appends one JSON line per event to transcripts under ~/.claude/projects.
On each render we parse only bytes appended since the last run and insert usage rows
keyed by message id + request id, which removes the duplicate lines written while a
response streams. Costs are not stored: they are computed at query time so a price
table update applies retroactively.
"""
import json
import sqlite3
import time
from datetime import datetime

from . import paths

TOKEN_COLUMNS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
SUMS = ", ".join(f"SUM({c})" for c in TOKEN_COLUMNS)
LOCAL_DAY = "date(ts, 'unixepoch', 'localtime')"


# The usage table is the only lasting record once Claude Code deletes old transcripts
# (cleanupPeriodDays), so schema changes must migrate rows in place, never drop them.
MIGRATIONS = {
    1: """
        CREATE TABLE files (path TEXT PRIMARY KEY, offset INTEGER NOT NULL);
        CREATE TABLE usage (
            key TEXT PRIMARY KEY, ts INTEGER NOT NULL, model TEXT, speed TEXT,
            input INTEGER, output INTEGER,
            cache_write_5m INTEGER, cache_write_1h INTEGER, cache_read INTEGER);
        CREATE INDEX usage_ts ON usage(ts);
    """,
    2: """
        ALTER TABLE usage ADD COLUMN session TEXT;
        CREATE INDEX usage_session ON usage(session);
    """,
    # Per-day rollup so month/year totals stay fast however large usage grows.
    3: f"""
        CREATE TABLE daily (
            day TEXT NOT NULL, model TEXT, speed TEXT,
            input INTEGER, output INTEGER,
            cache_write_5m INTEGER, cache_write_1h INTEGER, cache_read INTEGER,
            PRIMARY KEY (day, model, speed));
        INSERT INTO daily SELECT {LOCAL_DAY}, model, speed, {SUMS}
            FROM usage GROUP BY 1, 2, 3;
    """,
    # Latest known Pro/Max rate limit per window, shared by all sessions on this machine.
    4: """
        CREATE TABLE limits (window TEXT PRIMARY KEY, pct REAL NOT NULL, resets_at INTEGER NOT NULL);
    """,
    # When any session last received limits, to tell API billing apart from a resumed
    # subscriber session that simply hasn't had a response since restarting.
    5: """
        ALTER TABLE limits ADD COLUMN observed_at INTEGER NOT NULL DEFAULT 0;
    """,
}
SCHEMA_VERSION = max(MIGRATIONS)
COLUMNS = ("key", "ts", "session", "model", "speed") + TOKEN_COLUMNS


def connect(path=None):
    path = path or paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=1)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for v in range(version + 1, SCHEMA_VERSION + 1):
        with db:
            db.executescript(f"BEGIN; {MIGRATIONS[v]} PRAGMA user_version = {v}; COMMIT;")
    return db


def parse_line(line):
    """One transcript line -> usage row tuple, or None if it carries no usage."""
    if b'"usage"' not in line:
        return None
    try:
        d = json.loads(line)
        msg = d["message"]
        u = msg["usage"]
        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    created = u.get("cache_creation_input_tokens") or 0
    split = u.get("cache_creation") or {}
    w1h = split.get("ephemeral_1h_input_tokens") or 0
    w5m = split.get("ephemeral_5m_input_tokens", created - w1h) or 0
    return (
        f"{msg.get('id')}:{d.get('requestId')}", int(ts), d.get("sessionId"),
        msg.get("model"), u.get("speed"),
        u.get("input_tokens") or 0, u.get("output_tokens") or 0,
        w5m, w1h, u.get("cache_read_input_tokens") or 0,
    )


# Re-reading a line refreshes its row (so parser improvements apply on rescan) but keeps
# the first session id seen, since resumed sessions can copy earlier messages.
UPSERT = (
    f"INSERT INTO usage ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    "ON CONFLICT(key) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c not in ("key", "session"))
    + ", session = COALESCE(usage.session, excluded.session)"
)


def rescan(db):
    """Forget read offsets so every transcript is parsed again; existing rows are kept."""
    with db:
        db.execute("DELETE FROM files")


def ingest(db, root=None):
    root = root or paths.projects_dir()
    offsets = dict(db.execute("SELECT path, offset FROM files"))
    rows, new_offsets = [], []
    for path in root.glob("**/*.jsonl"):
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        start = offsets.get(key, 0)
        if size == start:
            continue
        if size < start:  # truncated or replaced: rescan, dedupe keeps it safe
            start = 0
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
        end = data.rfind(b"\n") + 1  # leave a partially written last line for next time
        if end == 0:
            continue
        for line in data[:end].splitlines():
            row = parse_line(line)
            if row:
                rows.append(row)
        new_offsets.append((key, start + end))
    if new_offsets:
        with db:
            db.executemany(UPSERT, rows)
            db.executemany("INSERT OR REPLACE INTO files VALUES (?,?)", new_offsets)
            refresh_daily(db, {r[1] for r in rows})


def refresh_daily(db, timestamps):
    """Recompute the daily rollup for the local days containing these timestamps."""
    days = {time.strftime("%Y-%m-%d", time.localtime(ts)) for ts in timestamps}
    for day in days:
        start = int(time.mktime(time.strptime(day, "%Y-%m-%d")))
        db.execute("DELETE FROM daily WHERE day = ?", (day,))
        # the ts range uses the index; the day check handles DST-length days exactly
        db.execute(
            f"INSERT INTO daily SELECT {LOCAL_DAY}, model, speed, {SUMS} FROM usage "
            f"WHERE ts BETWEEN ? AND ? AND {LOCAL_DAY} = ? GROUP BY 1, 2, 3",
            (start - 7200, start + 93600, day),
        )


def _totals(db, table, where, params):
    query = f"SELECT model, speed, {SUMS} FROM {table} WHERE {where} GROUP BY model, speed"
    return [
        (model, speed, dict(zip(TOKEN_COLUMNS, sums)))
        for model, speed, *sums in db.execute(query, params)
    ]


def totals_since(db, day):
    """Summed tokens per (model, speed) from a local date ('YYYY-MM-DD'), all sessions."""
    return _totals(db, "daily", "day >= ?", (day,))


# Within one window (same reset time, give or take clock jitter) usage only grows, so a
# lower value comes from an idle session's stale snapshot and is ignored. A later reset
# time means the window rolled over; an earlier one is a stale snapshot of the old window.
_UPDATE_LIMIT = """
    INSERT INTO limits (window, pct, resets_at) VALUES (?, ?, ?)
    ON CONFLICT(window) DO UPDATE SET pct = excluded.pct, resets_at = excluded.resets_at
    WHERE excluded.resets_at > limits.resets_at + 60
       OR (abs(excluded.resets_at - limits.resets_at) <= 60 AND excluded.pct > limits.pct)
"""


def update_limits(db, received, now):
    """received: {window: (pct, resets_at)} as sent by Claude Code for this session."""
    with db:
        db.executemany(_UPDATE_LIMIT, [(w, p, r) for w, (p, r) in received.items()])
        # refresh the observation time at most once a minute to avoid a write per render
        db.executemany("UPDATE limits SET observed_at = ? WHERE window = ? AND observed_at < ?",
                       [(int(now), w, int(now) - 60) for w in received])


def limits_last_observed(db):
    return db.execute("SELECT MAX(observed_at) FROM limits").fetchone()[0] or 0


def limits(db):
    """{window: (pct, resets_at)} for every window ever recorded."""
    return {w: (p, r) for w, p, r in db.execute("SELECT window, pct, resets_at FROM limits")}


def last_response(db, session_id):
    """Timestamp of the session's latest recorded response, or 0."""
    return db.execute("SELECT MAX(ts) FROM usage WHERE session = ?", (session_id,)).fetchone()[0] or 0


def first_day(db):
    """Earliest local date with recorded usage, or None when nothing is recorded."""
    return db.execute("SELECT MIN(day) FROM daily").fetchone()[0]


def totals_for_session(db, session_id):
    """Summed tokens per (model, speed) for one session, its subagents included."""
    return _totals(db, "usage", "session = ?", (session_id,))
