import json

import pytest


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
