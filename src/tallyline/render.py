import os
import time
from datetime import datetime

from . import pricing, store, update

PERIODS = ("today", "month", "year")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Gauges to watch while working come first, history last. ANSI styles by scope: this
# session in cyan, account-wide rate limits in magenta, this machine's totals in yellow.
# Cost figures are dimmed.
CYAN, YELLOW, MAGENTA = "\033[36m", "\033[33m", "\033[35m"
DIM, RESET = "\033[2m", "\033[0m"
GAP = "  "  # separates items within a scope; " │ " separates scopes


def fmt_tokens(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(int(n))


def fmt_usd(x):
    return f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}"


def range_start(name, now=None):
    """First local date of the period, as 'YYYY-MM-DD'."""
    d = (now or datetime.now()).date()
    if name in ("month", "year"):
        d = d.replace(day=1)
    if name == "year":
        d = d.replace(month=1)
    return d.isoformat()


def period_label(name, now=None):
    now = now or datetime.now()
    if name == "month":
        return MONTHS[now.month - 1]
    if name == "year":
        return str(now.year)
    return "today"


class Style:
    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, text, code):
        return f"{code}{text}{RESET}" if self.enabled else text

    def dim(self, text, resume):
        """Dim text inside a colored span, then resume that span's color."""
        return f"{DIM}{text}{RESET}{resume}" if self.enabled else text


def summarize(rows, prices):
    """(tokens, usd, has_unknown_model) for rows from store totals."""
    tokens, usd, unknown = 0, 0.0, False
    for model, speed, sums in rows:
        n = sum(sums.values())
        if not n:
            continue
        tokens += n
        c = pricing.cost(prices, model, speed, sums)
        if c is None:
            unknown = True
        else:
            usd += c
    return tokens, usd, unknown


def usage_text(tokens, usd, unknown, show_cost, style, color):
    text = f"{fmt_tokens(tokens)} tok"
    if show_cost:
        text += " " + style.dim(f"(≈{fmt_usd(usd)}{'+?' if unknown else ''})", color)
    return text


def fmt_context(n):
    """Context size in whole thousands: 384k, not 384.2k."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    return f"{n / 1e3:.0f}k" if n >= 1e3 else str(int(n))


def context_text(info):
    """'ctx 384k (38%)': tokens first, since cost scales with them, not with the percentage.

    The same percentage means very different sizes on 200k and 1M context windows.
    """
    cw = info.get("context_window") or {}
    cur = cw.get("current_usage")
    used = sum(cur.get(k) or 0 for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
               ) if cur else None
    pct = cw.get("used_percentage")
    if pct is None and used and cw.get("context_window_size"):
        pct = used / cw["context_window_size"] * 100
    if used and pct is not None:
        return f"ctx {fmt_context(used)} ({pct:.0f}%)"
    if pct is not None:
        return f"ctx {pct:.0f}%"
    return f"ctx {fmt_context(used)}" if used else None


WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
STALE_AFTER = 7 * 86400  # ignore records whose window reset over a week ago


def rate_limits(info, db, now=None):
    """'5h 23%  7d 41%' for Pro/Max subscribers, or None when billed by the API.

    Claude Code sends limits only after a session's first response, and drops a window
    once it resets. Recording the latest values across sessions lets a fresh session show
    them right away, and lets a reset window show 0% instead of disappearing.
    """
    now = now or time.time()
    sent = info.get("rate_limits") or {}
    received = {}
    for key, _ in WINDOWS:
        w = sent.get(key) or {}
        if w.get("used_percentage") is not None and w.get("resets_at") is not None:
            received[key] = (float(w["used_percentage"]), int(w["resets_at"]))
    if received:
        store.update_limits(db, received, now)
    elif info.get("session_id") and (
            store.last_response(db, info["session_id"]) > store.limits_last_observed(db) + 5):
        # A response arrived after the last time any session saw limits, yet none came
        # with it: billed by the API (e.g. a cancelled plan). A resumed session whose last
        # response predates that observation is just waiting for its first new response.
        return None
    recorded = {k: v for k, v in store.limits(db).items() if v[1] > now - STALE_AFTER}
    if not recorded:
        return None
    parts = []
    for key, label in WINDOWS:
        if key in recorded:
            pct, resets_at = recorded[key]
            parts.append(f"{label} {pct if resets_at > now else 0:.0f}%")
    return GAP.join(parts) or None


def render(info, db, cfg, color=None):
    if color is None:
        color = cfg.get("color", True) and not os.environ.get("NO_COLOR")
    style = Style(color)
    prices = pricing.load(cfg.get("prices"))
    show_cost = cfg["cost"]
    parts = []

    model = (info.get("model") or {}).get("display_name")
    if model:
        parts.append(f"[{model}]")

    # this session: context fill, plus session usage when billed by the API. Claude Code
    # sends rate limits only to Pro/Max subscribers, who get those instead.
    limits = rate_limits(info, db)
    session = [s for s in (context_text(info),) if s]
    if not limits and info.get("session_id"):
        rows = store.totals_for_session(db, info["session_id"])
        session.append(usage_text(*summarize(rows, prices), show_cost, style, CYAN))
    if session:
        parts.append(style(GAP.join(session), CYAN))

    # whole account, across devices and apps
    if limits:
        parts.append(style(limits, MAGENTA))

    # this machine: all sessions in the configured period(s)
    names = PERIODS if cfg["range"] == "all" else (
        cfg["range"] if cfg["range"] in PERIODS else "month",)
    # "*" marks a period that began before the earliest record, e.g. a year whose
    # early transcripts were deleted by Claude Code before tallyline was installed.
    first_day = store.first_day(db)
    totals = GAP.join(
        f"{period_label(n)}{'*' if first_day and first_day > range_start(n) else ''} "
        + usage_text(*summarize(store.totals_since(db, range_start(n)), prices),
                     show_cost, style, YELLOW)
        for n in names
    )
    parts.append(style(totals, YELLOW))

    newer = update.notice(cfg)
    if newer:
        parts.append(newer)
    return " │ ".join(parts)
