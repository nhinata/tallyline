import json

import pytest

NESTED = []
for _ in range(500):  # deep, but readable by json on every Python version
    NESTED = [NESTED]
# Wrong values for any field of JSON that tallyline reads but doesn't write. Session-id-like
# strings are relative to the id "s1": patterns and a different case that match its file.
BAD_VALUES = (None, True, 1, -1, 1.5, "", "x", [], {"a": 1}, NESTED, 10 ** 30, 10 ** 400,
              float("nan"), float("inf"), "9999-12-31T23:59:59Z", "a\ud83d", "a\x00b",
              "S1", "*", "?1", "[s]1", "**", "../proj/s1", "proj\\s1")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate Claude dir, config and cache under tmp_path."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    (tmp_path / "claude" / "projects" / "proj").mkdir(parents=True)
    # never start a real PyPI check from tests (release builds have a non-dev version)
    from tallyline import update
    monkeypatch.setattr(update, "_spawn_check", lambda: None)
    return tmp_path


def usage_line(msg_id, req_id, ts="2026-10-03T01:30:44.333Z", model="claude-sonnet-5",
               speed="standard", session="s1", inp=0, out=0, w5m=0, w1h=0, read=0):
    return json.dumps({
        "type": "assistant", "timestamp": ts, "requestId": req_id, "sessionId": session,
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": w5m + w1h, "cache_read_input_tokens": read,
            "cache_creation": {"ephemeral_5m_input_tokens": w5m, "ephemeral_1h_input_tokens": w1h},
            "speed": speed,
        }},
    }) + "\n"


def fail_open(monkeypatch, path, error):
    """Make open() raise error for one file; chmod doesn't block reads on Windows."""
    real_open = open

    def fake_open(file, *args, **kwargs):
        if str(file) == str(path.absolute()):
            raise error(file)
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr("builtins.open", fake_open)
