import os
from pathlib import Path


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def projects_dir():
    return claude_dir() / "projects"


def settings_path():
    return claude_dir() / "settings.json"


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "tallyline" / "config.json"


def db_path():
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "tallyline" / "usage.db"
