import time
from datetime import datetime, timedelta

from conftest import usage_line

from tallyline import config, paths, render, store


def iso(dt):
    return dt.astimezone().isoformat()


def setup_db(lines):
    (paths.projects_dir() / "proj" / "s.jsonl").write_text("".join(lines))
    db = store.connect()
    store.ingest(db)
    return db


def plain(info, db, **cfg):
    return render.render(info, db, {**config.load(), **cfg}, color=False)


MONTH = render.period_label("month")
# tests that record only today's usage see the month marked, except on the 1st
MONTH_SEEN_TODAY = MONTH + ("*" if datetime.now().day > 1 else "")
SOON = int(time.time()) + 3600
LATER = int(time.time()) + 5 * 86400
API_INFO = {
    "session_id": "s1",
    "model": {"display_name": "Opus 5.5"},
    "context_window": {"context_window_size": 200000, "used_percentage": 9.4},
}


def test_api_user_sees_session_usage(home):
    now = iso(datetime.now())
    db = setup_db([
        usage_line("a", "r", ts=now, session="s1", inp=1_000_000, out=1_000_000),
        usage_line("b", "r", ts=now, session="s2", inp=1_000_000),
    ])
    assert plain(API_INFO, db) == (
        f"[Opus 5.5] │ ctx 9%  2.0M tok (≈$12.00) │ {MONTH_SEEN_TODAY} 3.0M tok (≈$14.00)")


def test_subscriber_sees_account_rate_limits_last_instead_of_session_usage(home):
    db = setup_db([usage_line("a", "r", ts=iso(datetime.now()), inp=1_000_000)])
    info = {**API_INFO, "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": SOON},
                                        "seven_day": {"used_percentage": 41.2, "resets_at": LATER}}}
    assert plain(info, db) == (
        f"[Opus 5.5] │ ctx 9% │ {MONTH_SEEN_TODAY} 1.0M tok (≈$2.00) │ 5h 24%  7d 41%")


def test_partial_rate_limits(home):
    db = setup_db([])
    info = {"rate_limits": {"seven_day": {"used_percentage": 5, "resets_at": LATER}}}
    assert plain(info, db, cost=False) == f"{MONTH} 0 tok │ 7d 5%"


def test_all_ranges_and_unknown_model(home):
    now = datetime.now()
    db = setup_db([
        usage_line("a", "r", ts=iso(now), model="claude-mystery", inp=10),
        usage_line("b", "r", ts=iso(now.replace(month=1, day=1, hour=0, minute=0) - timedelta(days=1)),
                   inp=10_000),
    ])
    assert plain({}, db, range="all") == (
        f"today 10 tok (≈$0.00+?)  {MONTH} 10 tok (≈$0.00+?)  {now.year} 10 tok (≈$0.00+?)")


def test_zero_token_synthetic_rows_ignored(home):
    db = setup_db([usage_line("a", "r", ts=iso(datetime.now()), model="<synthetic>")])
    assert plain({}, db, cost=False) == f"{MONTH_SEEN_TODAY} 0 tok"


def test_colors_resume_after_dimmed_cost(home):
    db = setup_db([])
    out = render.render({}, db, {**config.load(), "range": "all"}, color=True)
    assert out.startswith("\033[33mtoday 0 tok \033[2m(≈$0.00)\033[0m\033[33m  ")


def test_no_color_env(home, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033" not in render.render({}, setup_db([]), config.load())


def test_context_percentage_computed_when_missing():
    info = {"context_window": {"context_window_size": 1000,
                               "current_usage": {"input_tokens": 250}}}
    assert render.context_pct(info) == "ctx 25%"
    assert render.context_pct({"context_window": {"context_window_size": 1000,
                                                  "current_usage": None}}) is None


def test_range_start_and_labels():
    now = datetime(2026, 10, 3, 15, 30)
    assert render.range_start("today", now) == "2026-10-03"
    assert render.range_start("month", now) == "2026-10-01"
    assert render.range_start("year", now) == "2026-01-01"
    assert [render.period_label(n, now) for n in render.PERIODS] == ["today", "Oct", "2026"]


def test_month_boundary_uses_local_days(home):
    db = setup_db([
        usage_line("a", "r", ts=iso(datetime(2026, 9, 30, 23, 59)), inp=1),
        usage_line("b", "r", ts=iso(datetime(2026, 10, 1, 0, 1)), inp=2),
    ])
    got = {d: n for (d, n) in db.execute("SELECT day, SUM(input) FROM daily GROUP BY day")}
    assert got == {"2026-09-30": 1, "2026-10-01": 2}


def test_period_marked_when_records_start_after_it_began(home):
    now = datetime.now()
    db = setup_db([usage_line("a", "r", ts=iso(now), inp=1)])
    first = now.date().isoformat()
    year_marked = first > f"{now.year}-01-01"
    month_marked = first > now.date().replace(day=1).isoformat()
    out = plain({}, db, range="all", cost=False)
    assert out == (f"today 1 tok  {MONTH}{'*' if month_marked else ''} 1 tok  "
                   f"{now.year}{'*' if year_marked else ''} 1 tok")


def test_no_marker_when_records_predate_period(home):
    now = datetime.now()
    db = setup_db([
        usage_line("old", "r", ts=iso(datetime(now.year - 1, 6, 1, 12)), inp=1),
        usage_line("a", "r", ts=iso(now), inp=1),
    ])
    assert plain({}, db, range="year", cost=False) == f"{now.year} 1 tok"


def limits_info(session="s1", **windows):
    return {"session_id": session, "rate_limits": {
        k: {"used_percentage": p, "resets_at": r} for k, (p, r) in windows.items()}}


def test_new_session_shows_recorded_limits_before_first_response(home):
    db = setup_db([usage_line("a", "r", ts=iso(datetime.now()), session="old")])
    render.rate_limits(limits_info("old", five_hour=(23, SOON), seven_day=(41, LATER)), db)
    assert render.rate_limits({"session_id": "new"}, db) == "5h 23%  7d 41%"


def test_reset_window_shows_zero_instead_of_disappearing(home):
    db = setup_db([])
    now = time.time()
    render.rate_limits(limits_info(five_hour=(80, int(now) + 10), seven_day=(41, LATER)), db, now=now)
    # an hour later the 5h window has reset and Claude Code sends only 7d
    assert render.rate_limits(limits_info(seven_day=(42, LATER)), db, now=now + 3600) == "5h 0%  7d 42%"


def test_stale_lower_value_from_idle_session_is_ignored(home):
    db = setup_db([])
    render.rate_limits(limits_info("b", seven_day=(45, LATER)), db)
    assert render.rate_limits(limits_info("a", seven_day=(41, LATER)), db) == "7d 45%"


def test_rollover_replaces_and_old_window_snapshot_is_ignored(home):
    db = setup_db([])
    render.rate_limits(limits_info(seven_day=(90, LATER)), db)
    render.rate_limits(limits_info(seven_day=(3, LATER + 7 * 86400)), db)   # new window
    render.rate_limits(limits_info(seven_day=(95, LATER)), db)              # stale old window
    assert store.limits(db)["seven_day"] == (3.0, LATER + 7 * 86400)


def test_reset_time_jitter_counts_as_same_window(home):
    db = setup_db([])
    render.rate_limits(limits_info(seven_day=(45, LATER)), db)
    render.rate_limits(limits_info(seven_day=(41, LATER + 5)), db)
    assert store.limits(db)["seven_day"][0] == 45


def test_response_after_last_limits_without_limits_is_api_billed(home):
    # e.g. someone who cancelled Pro/Max and now uses an API key
    db = setup_db([usage_line("a", "r", ts=iso(datetime.now()), session="s1", inp=1_000_000)])
    render.rate_limits(limits_info("old", seven_day=(41, LATER)), db, now=time.time() - 86400)
    assert render.rate_limits({"session_id": "s1"}, db) is None
    out = plain({"session_id": "s1"}, db)
    assert out.startswith("1.0M tok (≈$2.00) │ ")


def test_records_older_than_a_week_past_reset_are_ignored(home):
    db = setup_db([])
    now = time.time()
    render.rate_limits(limits_info(seven_day=(41, int(now) - 8 * 86400)), db, now=now - 9 * 86400)
    assert render.rate_limits({"session_id": "new"}, db, now=now) is None


def test_resumed_session_keeps_limits_until_its_first_new_response(home):
    # claude --continue: same session id with earlier responses, no limits sent yet
    earlier = datetime.now() - timedelta(minutes=10)
    db = setup_db([usage_line("a", "r", ts=iso(earlier), session="s1", inp=1)])
    render.rate_limits(limits_info("s1", seven_day=(41, LATER)), db,
                       now=earlier.timestamp() + 2)   # seen right after that response
    assert render.rate_limits({"session_id": "s1"}, db) == "7d 41%"
