import json

import pytest

from tallyline import cli, config, paths


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


def test_reinit_and_uninstall_recognize_any_tallyline_command(home):
    for command in ("tallyline", "/x/bin/tallyline", "'/a b/tallyline'", "python3 -m tallyline"):
        assert cli.is_ours(command), command
    assert not cli.is_ours("python3 ~/.claude/statusline.py")
    assert not cli.is_ours("ccusage statusline")
    paths.settings_path().write_text('{"statusLine": {"type": "command", "command": "tallyline"}}')
    cli.main(["init"])  # replacing our own older entry needs no --force
    cli.main(["uninstall"])
    assert settings() == {}
