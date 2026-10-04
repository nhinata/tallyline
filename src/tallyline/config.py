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


def load():
    try:
        with open(paths.config_path(), encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    return {**DEFAULTS, **user}


def save(cfg):
    path = paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in cfg.items() if DEFAULTS.get(k) != v}
    path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
