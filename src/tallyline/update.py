"""Once-a-day check for a newer tallyline release on PyPI.

This is tallyline's only network request. It sends nothing about the user: it fetches
the package's public JSON metadata and keeps only the version number. The render path
never waits on it; it spawns a detached process and shows the result on a later render.
"""
import json
import os
import re
import sys
import time

from . import __version__, paths

URL = "https://pypi.org/pypi/tallyline/json"
INTERVAL = 86400
_VERSION = re.compile(r"^\d+(\.\d+){1,3}$")


def state_path():
    return paths.db_path().with_name("update.json")


def _read_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _release(version):
    """'0.2.0' -> (0, 2, 0); None for dev/local builds, which never get notices."""
    return tuple(int(x) for x in version.split(".")) if _VERSION.match(version or "") else None


def enabled(cfg):
    return cfg.get("update_check", True) and not os.environ.get("NO_UPDATE_NOTIFIER")


def notice(cfg, now=None, current=__version__):
    """'↑ 0.2.0' when a newer release is known, else None. Starts a check when due."""
    if not enabled(cfg) or _release(current) is None:
        return None
    state = _read_state()
    now = now or time.time()
    if now - state.get("checked_at", 0) > INTERVAL:
        # record the attempt first so concurrent renders don't all spawn a check
        _write_state({**state, "checked_at": now})
        _spawn_check()
    latest = state.get("latest")
    if _release(latest) and _release(latest) > _release(current):
        return f"↑ {latest}"
    return None


def _spawn_check():
    import subprocess

    try:
        subprocess.Popen(
            [sys.executable, "-m", "tallyline", "_update-check"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def run_check():
    """Body of the detached process: fetch the latest version, keep it if well-formed."""
    import urllib.request

    req = urllib.request.Request(URL, headers={"User-Agent": f"tallyline/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            latest = json.load(r)["info"]["version"]
    except Exception:  # offline, blocked, or malformed: try again tomorrow
        return
    if _release(latest):
        _write_state({**_read_state(), "latest": latest})
