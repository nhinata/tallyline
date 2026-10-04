import json
import sqlite3
import sys

from . import __version__, config, paths, render, store

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
    import shlex

    try:
        words = shlex.split(command or "")
    except ValueError:
        return False
    return bool(words) and (words[0].rsplit("/", 1)[-1] == "tallyline"
                            or words[-2:] == ["-m", "tallyline"])


def cmd_render(_args):
    try:
        info = json.load(sys.stdin)
    except ValueError:
        info = {}
    cfg = config.load()
    try:
        db = store.connect()
        store.ingest(db)
        print(render.render(info, db, cfg))
    except sqlite3.Error as e:  # never break the status line over the cache
        print(f"tallyline: {e}")


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
    print(f"Added tallyline to {path}. Restart Claude Code to see it.")


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
    cfg = config.load()
    if args.key is None:
        print(json.dumps(cfg, indent=2))
        return
    if args.value is None:
        print(json.dumps(cfg.get(args.key.replace("-", "_"))))
        return
    if args.key == "range":
        if args.value not in config.RANGES:
            sys.exit(f"range must be one of: {', '.join(config.RANGES)}")
        cfg["range"] = args.value
    elif args.key in ("cost", "color", "update-check"):
        key = args.key.replace("-", "_")
        cfg[key] = args.value.lower() in ("1", "true", "on", "yes")
    else:
        sys.exit("settable keys: range, cost, color, update-check "
                 "(edit prices directly in the config file)")
    config.save(cfg)
    key = args.key.replace("-", "_")
    print(f"{args.key} = {json.dumps(cfg[key])}")


def cmd_rebuild(_args):
    db = store.connect()
    store.rescan(db)
    store.ingest(db)
    print(f"Re-scanned all transcripts into {paths.db_path()} (existing history kept)")


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
    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "uninstall": cmd_uninstall, "config": cmd_config,
                "rebuild": cmd_rebuild}
    handlers.get(args.cmd, cmd_render)(args)
