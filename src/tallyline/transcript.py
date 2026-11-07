"""Reading transcript lines, shared by the usage store, `diagnose` and `sessions`.

The transcript format is undocumented and a line can hold anything, so values are checked
here before they are used. A request that fails the checks is left out by all of them.
"""
import json
from collections import namedtuple
from datetime import datetime

MAX_TIMESTAMP = 4_102_444_800  # 2100-01-01 UTC, within every platform's local time range
MAX_TOKENS = 1 << 40  # per request, so that summing rows can't overflow SQLite's integers

# tokens: dict with input, output, cache_write_5m, cache_write_1h and cache_read
# context: input, cache writes and cache reads as the transcript totals them
Usage = namedtuple("Usage", "key ts session model speed tokens context")


def load_line(line):
    """The JSON object on one transcript line, or None if the line doesn't hold one."""
    try:
        d = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return d if isinstance(d, dict) else None


def _encodable(value):
    """False for a string holding a lone surrogate, which JSON can carry but UTF-8 can't."""
    try:
        value.encode()
    except UnicodeEncodeError:
        return False
    return True


def text(d, key):
    """The non-empty, printable string under key, or None for a missing or invalid value."""
    value = d.get(key) if isinstance(d, dict) else None
    return value if isinstance(value, str) and value and _encodable(value) else None


def parse_timestamp(value):
    """Seconds since the epoch for an ISO 8601 string, or None if invalid or out of range."""
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, OverflowError, OSError):
        return None
    return ts if 0 < ts < MAX_TIMESTAMP else None


def parse_usage(d):
    """The Usage of one loaded transcript line, or None if it carries no valid usage."""
    try:
        msg = d["message"]
        u = msg["usage"]
        created = u.get("cache_creation_input_tokens") or 0
        split = u.get("cache_creation") or {}
        w1h = split.get("ephemeral_1h_input_tokens") or 0
        w5m = split.get("ephemeral_5m_input_tokens", created - w1h) or 0
        texts = (d.get("sessionId"), msg.get("model"), u.get("speed"))
        tokens = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
                  "cache_write_5m": w5m, "cache_write_1h": w1h,
                  "cache_read": u.get("cache_read_input_tokens") or 0}
        key = f"{msg.get('id')}:{d.get('requestId')}"
    except (KeyError, TypeError, AttributeError):
        return None
    ts = parse_timestamp(d.get("timestamp"))
    if (ts is None
            or not _encodable(key)
            or not all(t is None or (isinstance(t, str) and _encodable(t)) for t in texts)
            or not all(type(n) is int and 0 <= n < MAX_TOKENS
                       for n in (created, *tokens.values()))):
        return None
    return Usage(key, ts, *texts, tokens, tokens["input"] + created + tokens["cache_read"])
