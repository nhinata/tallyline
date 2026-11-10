import json
import sys
import time
from pathlib import Path

from . import __version__, config, paths, render, store

ON_WORDS = ("1", "true", "on", "yes")
OFF_WORDS = ("0", "false", "off", "no")


def status_command():
    """Absolute command for settings.json.

    Claude Code launched from a desktop app or IDE may not have pipx's bin directory on
    PATH, so a bare "tallyline" could leave the status line blank.
    """
    import shlex
    import shutil

    found = shutil.which("tallyline")
    if found:
        return shlex.quote(found)
    return f"{shlex.quote(sys.executable)} -m tallyline"


def is_ours(command):
    import re
    import shlex

    try:
        words = shlex.split(command or "")
    except ValueError:
        return False
    if not words:
        return False
    name = re.split(r"[\\/]", words[0])[-1].lower()  # Windows paths use backslashes
    return name in ("tallyline", "tallyline.exe") or words[-2:] == ["-m", "tallyline"]


def cmd_render(_args):
    try:
        info = json.load(sys.stdin)
    except ValueError:
        info = {}
    try:
        cfg = config.load()
        db = store.connect()
        store.ingest(db)
        print(render.render(info, db, cfg))
    except Exception:  # never leave the status line blank; keep the details for a bug report
        print(f"tallyline: error (see {_log_error()})")


def _log_error():
    """Append the current traceback to error.log next to the cache and return its path."""
    import traceback

    path = paths.db_path().with_name("error.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1 << 20:
            path.write_text("", encoding="utf-8")  # keep it small
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} tallyline {__version__}\n")
            f.write(traceback.format_exc())
    except OSError:
        pass
    return _display_path(path)


def _display_path(path):
    home = Path.home()
    try:
        return str(Path("~") / Path(path).relative_to(home))
    except ValueError:  # outside home, including /Users/name2 when home is /Users/name
        return str(path)


def _read_settings(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        sys.exit(f"{path} is not valid JSON; add this manually:\n"
                 + json.dumps({"statusLine": {"type": "command", "command": status_command()}}))


def cmd_init(args):
    path = paths.settings_path()
    settings = _read_settings(path)
    current = settings.get("statusLine")
    if current and not is_ours(current.get("command")) and not args.force:
        sys.exit(f"statusLine is already set to {current.get('command')!r} in {path}.\n"
                 "Re-run with --force to replace it.")
    if path.exists():
        import shutil

        shutil.copy2(path, path.with_name(path.name + ".bak"))
    settings["statusLine"] = {"type": "command", "command": status_command()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Added tallyline to {path}. Claude Code picks it up without a restart.")


def cmd_uninstall(_args):
    path = paths.settings_path()
    settings = _read_settings(path)
    if not is_ours((settings.get("statusLine") or {}).get("command")):
        print("tallyline is not configured as the status line; nothing to do.")
        return
    import shutil

    shutil.copy2(path, path.with_name(path.name + ".bak"))
    del settings["statusLine"]
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Removed tallyline from {path}. Cache remains at {paths.db_path()}.")


def cmd_config(args):
    try:
        cfg = config.load(strict=True)
    except (OSError, ValueError):
        sys.exit(f"{paths.config_path()} can't be read as a JSON object; fix or delete it first.")
    if args.key is None:
        print(json.dumps(cfg, indent=2))
        return
    if args.value is None:
        key = args.key.replace("-", "_")
        if key not in config.DEFAULTS:
            sys.exit(f"unknown key: {args.key}")
        print(json.dumps(cfg[key]))
        return
    if args.key == "range":
        if args.value not in config.RANGES:
            sys.exit(f"range must be one of: {', '.join(config.RANGES)}")
        cfg["range"] = args.value
    elif args.key in ("cost", "color", "update-check"):
        value = args.value.lower()
        if value not in ON_WORDS + OFF_WORDS:
            sys.exit(f"{args.key} must be on or off")
        cfg[args.key.replace("-", "_")] = value in ON_WORDS
    else:
        sys.exit("settable keys: range, cost, color, update-check "
                 "(edit prices directly in the config file)")
    config.save(cfg)
    key = args.key.replace("-", "_")
    print(f"{args.key} = {json.dumps(cfg[key])}")


def cmd_rebuild(_args):
    db = store.connect()
    unreadable = store.rebuild(db)
    if unreadable is None:
        sys.exit(f"Another tallyline process kept the cache locked for "
                 f"{store.REBUILD_WAIT_MS // 1000} seconds; nothing was changed. Try again.")
    print(f"Re-scanned all transcripts into {_display_path(paths.db_path())} "
          "(existing history kept)")
    if unreadable:
        print(f"Could not read {len(unreadable)} transcript(s); their usage is not counted:",
              file=sys.stderr)
        for path, reason in unreadable:
            print(f"  {_display_path(path)}: {reason}", file=sys.stderr)
        sys.exit(1)


def cmd_diagnose(args):
    from . import diagnose, pricing

    if args.days <= 0:
        sys.exit("--days must be a positive number")
    prices = pricing.load(config.load().get("prices"))
    try:
        print(diagnose.run(days=args.days, session=args.session, as_json=args.json,
                           show_titles=not args.no_titles, prices=prices))
    except LookupError as e:
        sys.exit(str(e))


def cmd_sessions(args):
    from . import pricing, sessions

    prices = pricing.load(config.load().get("prices"))
    print(sessions.run(as_json=args.json, show_titles=not args.no_titles, sort=args.sort,
                       prices=prices))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:  # the status line hot path: skip argparse and its imports
        cmd_render(None)
        return
    if argv == ["_update-check"]:  # detached background process started by render
        from . import update

        update.run_check()
        return
    import argparse

    parser = argparse.ArgumentParser(
        prog="tallyline",
        description="Claude Code status line with token and API-equivalent cost totals.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("render", help="render the status line from stdin JSON (default)")
    p = sub.add_parser("init", help="register tallyline in Claude Code settings.json")
    p.add_argument("--force", action="store_true", help="replace an existing statusLine")
    sub.add_parser("uninstall", help="remove tallyline from settings.json")
    p = sub.add_parser("config", help="show or change settings")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    sub.add_parser("rebuild", help="re-scan all transcripts (keeps history of deleted ones)")
    p = sub.add_parser("diagnose", help="show what drove token usage and how to cut it")
    p.add_argument("--days", type=int, default=30, help="look back this many days (default 30)")
    p.add_argument("--session", metavar="ID", help="break down one session (an id prefix works)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-titles", action="store_true",
                   help="hide session titles, projects and file paths")
    p = sub.add_parser("sessions", help="list open sessions and what resuming each one costs")
    p.add_argument("--sort", choices=("recent", "resume"), default="recent",
                   help="order by last message (default) or by resume cost")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-titles", action="store_true",
                   help="hide session titles, directories and tmux window names")
    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "uninstall": cmd_uninstall, "config": cmd_config,
                "rebuild": cmd_rebuild, "diagnose": cmd_diagnose, "sessions": cmd_sessions}
    handlers.get(args.cmd, cmd_render)(args)
