import json

from . import paths

RANGES = ("today", "month", "year", "all")
DEFAULTS = {
    "range": "month",   # today | month | year | all
    "cost": True,       # show API-equivalent cost next to token totals
    "color": True,      # ANSI colors (also disabled by the NO_COLOR env var)
    "update_check": True,  # daily PyPI version check (also disabled by NO_UPDATE_NOTIFIER)
    "prices": {},       # per-model price overrides, same shape as prices.json entries
}


def load(strict=False):
    """Settings over the defaults.

    A file that can't be read as a JSON object counts as empty, or raises OSError or
    ValueError when strict, since saving after a lenient load would overwrite that file.
    """
    try:
        with open(paths.config_path(), encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        user = {}
    except RecursionError:
        if strict:
            raise ValueError("nested too deeply") from None
        user = {}
    except (OSError, ValueError):
        if strict:
            raise
        user = {}
    return {**DEFAULTS, **user}


def save(cfg):
    path = paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in cfg.items() if DEFAULTS.get(k) != v}
    path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
