import json

import pytest
from conftest import BAD_VALUES, fail_open, usage_line

from tallyline import cli, config, paths, store


def settings():
    return json.loads(paths.settings_path().read_text())


@pytest.fixture(autouse=True)
def installed_at(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/home/u/.local/bin/tallyline")


def test_init_merges_into_existing_settings_and_backs_up(home):
    paths.settings_path().write_text('{"theme": "auto"}')
    cli.main(["init"])
    assert settings() == {"theme": "auto", "statusLine": {
        "type": "command", "command": "/home/u/.local/bin/tallyline"}}
    assert json.loads(paths.settings_path().with_name("settings.json.bak").read_text()) == {
        "theme": "auto"}


def test_init_refuses_to_replace_other_status_line_without_force(home):
    paths.settings_path().write_text('{"statusLine": {"type": "command", "command": "other"}}')
    with pytest.raises(SystemExit):
        cli.main(["init"])
    assert settings()["statusLine"]["command"] == "other"
    cli.main(["init", "--force"])
    assert settings()["statusLine"]["command"] == "/home/u/.local/bin/tallyline"


def test_init_leaves_invalid_json_untouched(home):
    paths.settings_path().write_text("{broken")
    with pytest.raises(SystemExit):
        cli.main(["init"])
    assert paths.settings_path().read_text() == "{broken"


def test_uninstall_removes_only_our_entry(home):
    paths.settings_path().write_text('{"theme": "auto"}')
    cli.main(["init"])
    cli.main(["uninstall"])
    assert settings() == {"theme": "auto"}


def test_config_set_and_validate(home):
    cli.main(["config", "range", "year"])
    cli.main(["config", "cost", "off"])
    assert config.load()["range"] == "year"
    assert config.load()["cost"] is False
    with pytest.raises(SystemExit):
        cli.main(["config", "range", "week"])


@pytest.mark.parametrize("value, expected", [
    ("on", True), ("ON", True), ("true", True), ("yes", True), ("1", True),
    ("off", False), ("false", False), ("no", False), ("0", False),
])
def test_config_accepts_on_and_off_words(home, value, expected):
    cli.main(["config", "cost", "off" if expected else "on"])
    cli.main(["config", "cost", value])
    assert config.load()["cost"] is expected


def test_config_rejects_a_mistyped_value_and_keeps_the_setting(home):
    with pytest.raises(SystemExit) as e:
        cli.main(["config", "cost", "onn"])
    assert "on or off" in str(e.value)
    assert config.load()["cost"] is True


def test_config_rejects_reading_an_unknown_key(home):
    with pytest.raises(SystemExit) as e:
        cli.main(["config", "nonsense"])
    assert "nonsense" in str(e.value)


def test_config_rejects_reading_an_unsupported_key_from_the_file(home):
    path = paths.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"update_chek": false}\n')
    with pytest.raises(SystemExit) as e:
        cli.main(["config", "update_chek"])
    assert "update_chek" in str(e.value)


@pytest.mark.parametrize("broken", ['{"prices": {},}\n', "[]\n"])
def test_config_leaves_a_broken_file_alone(home, broken):
    path = paths.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(broken)
    for args in (["config", "cost", "off"], ["config", "cost"], ["config"]):
        with pytest.raises(SystemExit) as e:
            cli.main(args)
        assert str(path) in str(e.value)
    assert path.read_text() == broken


def test_config_stops_when_the_file_cannot_be_read(home):
    paths.config_path().mkdir(parents=True)  # open() fails on every platform
    with pytest.raises(SystemExit) as e:
        cli.main(["config", "cost", "off"])
    assert str(paths.config_path()) in str(e.value)
    assert config.load() == config.DEFAULTS


def test_status_line_falls_back_to_defaults_on_a_broken_config(home):
    path = paths.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("[]\n")
    assert config.load() == config.DEFAULTS


def test_render_survives_empty_stdin(home, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    cli.main([])
    from tallyline import render
    label = render.period_label("month")
    assert capsys.readouterr().out.strip() == f"\033[33m{label} 0 tok \033[2m(≈$0.00)\033[0m\033[33m\033[0m"


def test_init_falls_back_to_python_module_when_not_on_path(home, monkeypatch):
    import sys
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(sys, "executable", "/opt/py dir/bin/python3")
    cli.main(["init"])
    assert settings()["statusLine"]["command"] == "'/opt/py dir/bin/python3' -m tallyline"


@pytest.mark.parametrize("found", [r"C:\Users\A B\Scripts\tallyline.EXE",
                                   r"C:\Python\Scripts\tallyline.exe", None])
def test_the_command_init_writes_is_recognized_with_windows_paths(monkeypatch, found):
    import sys
    monkeypatch.setattr("shutil.which", lambda name: found)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Python\python.exe")
    assert cli.is_ours(cli.status_command())


def test_reinit_and_uninstall_recognize_any_tallyline_command(home):
    for command in ("tallyline", "/x/bin/tallyline", "'/a b/tallyline'", "python3 -m tallyline"):
        assert cli.is_ours(command), command
    assert not cli.is_ours("python3 ~/.claude/statusline.py")
    assert not cli.is_ours("ccusage statusline")
    paths.settings_path().write_text('{"statusLine": {"type": "command", "command": "tallyline"}}')
    cli.main(["init"])  # replacing our own older entry needs no --force
    cli.main(["uninstall"])
    assert settings() == {}


def test_render_error_shows_a_short_line_and_logs_the_traceback(home, monkeypatch, capsys):
    import io
    from tallyline import render

    def broken(*args, **kwargs):
        raise KeyError("unexpected field")

    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setattr(render, "render", broken)
    cli.main([])
    out = capsys.readouterr().out.strip()
    assert out.startswith("tallyline: error (see ") and out.endswith("error.log)")
    log = paths.db_path().with_name("error.log").read_text()
    assert "KeyError: 'unexpected field'" in log and "tallyline" in log


def test_rebuild_lists_unreadable_transcripts_and_fails(home, monkeypatch, capsys):
    log = paths.projects_dir() / "proj" / "a.jsonl"
    log.write_text("{}\n")
    fail_open(monkeypatch, log, lambda f: PermissionError(13, "Permission denied", f))
    with pytest.raises(SystemExit) as e:
        cli.main(["rebuild"])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "Could not read 1 transcript(s)" in err and "a.jsonl: Permission denied" in err


def test_rebuild_fails_without_changes_while_the_cache_is_locked(home, monkeypatch, capsys):
    monkeypatch.setattr(store, "REBUILD_WAIT_MS", 10)
    other = store.connect()
    other.isolation_level = None
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SystemExit) as e:
            cli.main(["rebuild"])
    finally:
        other.execute("ROLLBACK")
    assert "nothing was changed" in str(e.value.code)
    assert "Re-scanned" not in capsys.readouterr().out


def test_rebuild_succeeds_when_every_transcript_is_read(home, capsys):
    cli.main(["rebuild"])
    assert "Re-scanned" in capsys.readouterr().out


def test_paths_are_shortened_only_inside_home(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path / "me")
    assert cli._display_path(tmp_path / "me" / "a.db") == str(cli.Path("~") / "a.db")
    assert cli._display_path(tmp_path / "me2" / "a.db") == str(tmp_path / "me2" / "a.db")


def status_line(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    capsys.readouterr()
    cli.main([])
    return capsys.readouterr().out


def test_deeply_nested_config_counts_as_empty_except_for_the_config_command(
        home, monkeypatch, capsys):
    # deep enough that json can't read it on any Python version
    paths.config_path().parent.mkdir(parents=True)
    paths.config_path().write_text('{"prices": ' + "[" * 10 ** 6)
    assert config.load() == config.DEFAULTS
    assert "tallyline: error" not in status_line(monkeypatch, capsys)
    cli.main(["sessions"])
    with pytest.raises(SystemExit):
        cli.main(["config", "range", "today"])
    assert paths.config_path().read_text().startswith('{"prices": [[')


def test_wrong_prices_in_config_never_break_the_commands(home, monkeypatch, capsys):
    (paths.projects_dir() / "proj" / "s1.jsonl").write_text(usage_line("m", "r", inp=1_000))
    paths.config_path().parent.mkdir(parents=True)
    for bad in BAD_VALUES:
        for prices in (bad, {"claude-sonnet-5": bad}, {"claude-sonnet-5": {"input": bad}}):
            paths.config_path().write_text(json.dumps({"prices": prices}))
            line = status_line(monkeypatch, capsys)
            assert "tallyline: error" not in line and "nan" not in line and "inf" not in line
            for argv in (["sessions", "--json"], ["diagnose", "--json"]):
                cli.main(argv)
                out = capsys.readouterr().out
                assert "NaN" not in out and "Infinity" not in out, (prices, argv)
