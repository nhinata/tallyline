"""Model price table: bundled defaults, user overrides, and the parser that builds them.

Prices are USD per million tokens. The bundled table (prices.json) is generated from
Anthropic's official pricing page by scripts/update_prices.py; it is never fetched at runtime.
"""
import json
import re
from pathlib import Path

# Price fields and the pricing page column each one is read from, in page order.
_PAGE_COLUMNS = (
    ("Base input tokens", "input"),
    ("5m cache writes", "cache_write_5m"),
    ("1h cache writes", "cache_write_1h"),
    ("Cache hits and refreshes", "cache_read"),
    ("Output tokens", "output"),
)
FIELDS = tuple(field for _, field in _PAGE_COLUMNS)
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


def rates(prices, model, speed):
    """USD per token for each field of one model, or None if the model is unknown."""
    p = prices.get(normalize_model(model))
    if p is None or any(f not in p for f in FIELDS):
        return None
    mult = p.get("fast_multiplier", 1.0) if speed == "fast" else 1.0
    return {f: p[f] * mult / 1e6 for f in FIELDS}


def cost(prices, model, speed, tokens):
    """USD cost for summed token counts of one model, or None if the model is unknown.

    tokens: dict with input, output, cache_write_5m, cache_write_1h, cache_read.
    """
    r = rates(prices, model, speed)
    if r is None:
        return None
    return sum(tokens.get(f, 0) * r[f] for f in FIELDS)


# --- parsing the official pricing page (used by scripts/update_prices.py and tests) ---

_PRICE = re.compile(r"\$(\d+(?:\.\d+)?)(?: USD)? / MTok(?:<sup>\d+</sup>)?")
_SEPARATOR = re.compile(r":?-+:?")
_FENCE = re.compile(r"```|~~~")
_HEADING = re.compile(r" {0,3}(#+) ")
_MODEL_TABLE = ("Model",) + tuple(column for column, _ in _PAGE_COLUMNS)
_FAST_TABLE = ("Model", "Input", "Output")


_MODEL_ID = re.compile(r"^claude-[a-z]+(-\d+)+$")


def _model_id(name):
    """'Claude Opus 4.1 ([retired, ... (Google managed)](...))' -> 'claude-opus-4-1'"""
    # Model names never contain parentheses; notes after them may nest their own.
    name = name.split("(", 1)[0]
    name = re.sub(r"<sup>.*?</sup>", "", name).strip()
    return re.sub(r"[\s.]+", "-", name.lower())


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _price(cell, row):
    m = _PRICE.fullmatch(cell)
    if not m:
        raise ValueError(f"unreadable price for {row}: {cell!r}")
    return float(m.group(1))


def _table_rows(table, columns, heading):
    """The data rows of one markdown table, after checking it against the expected columns."""
    if tuple(table[0]) != columns:
        raise ValueError(f"unexpected table header under {heading}: {table[0]}")
    if len(table) < 2 or len(table[1]) != len(columns) or not all(
        _SEPARATOR.fullmatch(c) for c in table[1]
    ):
        raise ValueError(f"unexpected table separator under: {heading}")
    if len(table) < 3:
        raise ValueError(f"table without rows under: {heading}")
    for row in table[2:]:
        if len(row) != len(columns):
            raise ValueError(f"unexpected number of cells under {heading}: {row[0]}")
    return table[2:]


def _section_rows(lines, heading, columns):
    """Rows (as cell lists) of every table in a section, each checked against columns.

    The section runs from its heading to the next heading of the same or a higher level.
    Sections and tables are found by the first characters of a line only; as in markdown, a
    heading may be indented by up to three spaces. A sub-heading or a code block inside the
    section raises; other lines between tables are ignored.
    """
    starts = [i for i, l in enumerate(lines) if l.strip().lower() == heading.lower()]
    if len(starts) != 1:
        raise ValueError(f"expected one heading, found {len(starts)}: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    tables, in_table = [], False
    for line in lines[starts[0] + 1:]:
        if _FENCE.match(line.lstrip()):
            raise ValueError(f"code block under: {heading}")
        if line.lstrip().startswith("|"):
            if not in_table:
                tables.append([])
            tables[-1].append(_cells(line))
            in_table = True
            continue
        in_table = False
        m = _HEADING.match(line)
        if m:
            if len(m.group(1)) <= level:
                break
            raise ValueError(f"sub-heading under {heading}: {line}")
    if not tables:
        raise ValueError(f"no table under: {heading}")
    return [row for table in tables for row in _table_rows(table, columns, heading)]


def parse_pricing_page(markdown):
    """Build the models price table from the pricing page markdown.

    Every row of the model and fast mode tables is read (except the long-prompt tier), and a
    table or row that does not fit the expected layout raises ValueError.
    """
    lines = markdown.splitlines()
    models, long_prompt = {}, set()
    for row in _section_rows(lines, "## Model pricing", _MODEL_TABLE):
        # Some models are priced by prompt length ("for prompts over 100,000 tokens");
        # the table holds one price per model, so keep the standard tier. If the wording
        # changes, the other tier fails below as a second row for the same model.
        if re.search(r"for prompts over", row[0], re.IGNORECASE):
            long_prompt.add(_model_id(row[0]))
            continue
        model = _model_id(row[0])
        if model in models:
            raise ValueError(f"more than one row for: {model}")
        models[model] = {field: _price(c, row[0]) for (_, field), c in zip(_PAGE_COLUMNS, row[1:])}
    missing = sorted(long_prompt - models.keys())
    if missing:
        raise ValueError(f"only a long-prompt tier for: {', '.join(missing)}")

    # fast_multiplier applies to every field, so fast mode must scale input and output alike
    for row in _section_rows(lines, "### Fast mode pricing", _FAST_TABLE):
        fast_input, fast_output = _price(row[1], row[0]), _price(row[2], row[0])
        for name in row[0].split(" / "):
            model = _model_id(name)
            p = models.get(model)
            if p is None:
                raise ValueError(f"unknown fast mode model: {model}")
            if not p["input"] or not p["output"]:
                raise ValueError(f"no standard price for fast mode model: {model}")
            if "fast_multiplier" in p:
                raise ValueError(f"more than one fast mode row for: {model}")
            multiplier = round(fast_input / p["input"], 4)
            if multiplier != round(fast_output / p["output"], 4):
                raise ValueError(f"fast mode input and output multipliers differ for: {model}")
            p["fast_multiplier"] = multiplier

    if not models:
        raise ValueError("no model prices parsed")
    bad = sorted(m for m in models if not _MODEL_ID.match(m))
    if bad:
        raise ValueError(f"unexpected model names: {', '.join(bad)}")
    return models
