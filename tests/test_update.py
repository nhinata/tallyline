import io
import json

from tallyline import cli, config, update


def cfg(**kw):
    return {**config.load(), **kw}


def test_notice_when_newer_release_is_known(home, monkeypatch):
    monkeypatch.setattr(update, "_spawn_check", lambda: None)
    update._write_state({"checked_at": 1e12, "latest": "0.3.0"})
    assert update.notice(cfg(), now=1e12, current="0.2.0") == "↑ 0.3.0"
    assert update.notice(cfg(), now=1e12, current="0.3.0") is None


def test_no_notice_for_dev_builds_or_when_disabled(home, monkeypatch):
    monkeypatch.setattr(update, "_spawn_check", lambda: None)
    update._write_state({"checked_at": 1e12, "latest": "9.0.0"})
    assert update.notice(cfg(), now=1e12, current="0.0.1.dev0+d20261004") is None
    assert update.notice(cfg(update_check=False), now=1e12, current="0.1.0") is None
    monkeypatch.setenv("NO_UPDATE_NOTIFIER", "1")
    assert update.notice(cfg(), now=1e12, current="0.1.0") is None


def test_check_is_spawned_at_most_once_a_day(home, monkeypatch):
    spawned = []
    monkeypatch.setattr(update, "_spawn_check", lambda: spawned.append(1))
    update.notice(cfg(), now=1_000_000, current="0.1.0")
    update.notice(cfg(), now=1_000_000 + 3600, current="0.1.0")
    assert len(spawned) == 1
    update.notice(cfg(), now=1_000_000 + 2 * 86400, current="0.1.0")
    assert len(spawned) == 2


def test_run_check_keeps_only_well_formed_versions(home, monkeypatch):
    def fake(version):
        return lambda req, timeout: io.BytesIO(json.dumps({"info": {"version": version}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake("0.4.0"))
    update.run_check()
    assert update._read_state()["latest"] == "0.4.0"
    monkeypatch.setattr("urllib.request.urlopen", fake("<script>"))
    update.run_check()
    assert update._read_state()["latest"] == "0.4.0"


def test_run_check_survives_network_failure(home, monkeypatch):
    def boom(req, timeout):
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    update.run_check()
    assert "latest" not in update._read_state()


def test_config_update_check_toggle(home):
    cli.main(["config", "update-check", "off"])
    assert config.load()["update_check"] is False
