"""Model price table: bundled defaults, user overrides, and the parser that builds them.

Prices are USD per million tokens. The bundled table (prices.json) is generated from
Anthropic's official pricing page by scripts/update_prices.py; it is never fetched at runtime.
"""
import json
import re
from pathlib import Path

FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def load_bundled():
    # read via __file__ rather than importlib.resources, which costs ~9 ms to import
    text = (Path(__file__).parent / "prices.json").read_text(encoding="utf-8")
    return json.loads(text)["models"]


def load(overrides=None):
    """Bundled prices with per-model overrides from the user config merged on top."""
    prices = load_bundled()
    for model, fields in (overrides or {}).items():
        prices[model] = {**prices.get(model, {}), **fields}
    return prices


def normalize_model(model):
    """claude-haiku-4-5-20251001 -> claude-haiku-4-5"""
    return _DATE_SUFFIX.sub("", model or "")


def cost(prices, model, speed, tokens):
    """USD cost for summed token counts of one model, or None if the model is unknown.

    tokens: dict with input, output, cache_write_5m, cache_write_1h, cache_read.
    """
    p = prices.get(normalize_model(model))
    if p is None or any(f not in p for f in FIELDS):
        return None
    mult = p.get("fast_multiplier", 1.0) if speed == "fast" else 1.0
    return sum(tokens.get(f, 0) * p[f] for f in FIELDS) * mult / 1e6


# --- parsing the official pricing page (used by scripts/update_prices.py and tests) ---

_PRICE = re.compile(r"\$([\d.]+)\s*/\s*MTok")


def _model_id(name):
    """'Claude Opus 5.5 ([retired...](...))' -> 'claude-opus-5-5'"""
    name = re.sub(r"\(.*?\)\)?", "", name)  # drop parenthetical notes and links
    name = re.sub(r"<sup>.*?</sup>", "", name).strip()
    return re.sub(r"[\s.]+", "-", name.lower())


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_after(lines, heading):
    """Rows (as cell lists) of the first markdown table after a heading line."""
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == heading.lower())
    except StopIteration:
        raise ValueError(f"heading not found: {heading}")
    rows = []
    for line in lines[start + 1:]:
        if line.lstrip().startswith("|"):
            rows.append(_cells(line))
        elif rows:
            break
        elif line.startswith("#"):
            break
    if len(rows) < 3:
        raise ValueError(f"no table under: {heading}")
    return rows[2:]  # skip header and separator


def parse_pricing_page(markdown):
    """Build the models price table from the pricing page markdown."""
    lines = markdown.splitlines()
    models = {}
    for row in _table_after(lines, "## Model pricing"):
        if len(row) < 6:
            continue
        # Some models are priced by prompt length ("for prompts over 100,000 tokens");
        # the table holds one price per model, so keep the standard tier.
        if re.search(r"for prompts over", row[0], re.IGNORECASE):
            continue
        values = [_PRICE.search(c) for c in row[1:6]]
        if not all(values):
            continue
        # column order on the page matches FIELDS: input, 5m write, 1h write, cache hit, output
        models[_model_id(row[0])] = dict(zip(FIELDS, (float(v.group(1)) for v in values)))

    try:
        fast_rows = _table_after(lines, "### Fast mode pricing")
    except ValueError:
        fast_rows = []
    for row in fast_rows:
        m = _PRICE.search(row[1]) if len(row) > 1 else None
        if not m:
            continue
        for name in row[0].split(" / "):
            mid = _model_id(name)
            if mid in models and models[mid]["input"]:
                models[mid]["fast_multiplier"] = round(float(m.group(1)) / models[mid]["input"], 4)

    if not models:
        raise ValueError("no model prices parsed")
    return models
