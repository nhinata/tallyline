import pytest
from conftest import BAD_VALUES

from tallyline import pricing

MODEL_HEADER = (
    "| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
)

PAGE = """
## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock](https://x/y)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Sonnet 5 | $2 / MTok<sup>3</sup> | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok<sup>3</sup> |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |

*footnote*

## Feature-specific pricing

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Opus 5.5 | $8 USD / MTok | $40 USD / MTok |
| Claude Opus 5 / Claude Opus 4.1 | $10 USD / MTok | $50 USD / MTok |
"""


def test_parse_pricing_page():
    models = pricing.parse_pricing_page(PAGE)
    assert set(models) == {"claude-opus-5-5", "claude-opus-4-1", "claude-sonnet-5", "claude-opus-5"}
    assert models["claude-opus-5-5"] == {
        "input": 4.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0,
        "cache_read": 0.2, "output": 20.0, "fast_multiplier": 2.0,
    }
    assert models["claude-sonnet-5"]["output"] == 10.0
    assert "fast_multiplier" not in models["claude-sonnet-5"]
    # one row can price several models, each against its own standard price
    assert models["claude-opus-5"]["fast_multiplier"] == 2.0
    assert models["claude-opus-4-1"]["fast_multiplier"] == round(10 / 15, 4)


@pytest.mark.parametrize("old, new", [
    ("| Claude Opus 5 / Claude Opus 4.1 |",
     "\n| Model | Input | Output |\n| --- | --- | --- |\n| Claude Opus 5 / Claude Opus 4.1 |"),
    ("## Feature-specific pricing", "   ## Feature-specific pricing"),
    ("| Claude Sonnet 5 |",
     "\n    # an indented code example, not a heading\n\n" + MODEL_HEADER + "| Claude Sonnet 5 |"),
], ids=["table-split-in-two", "indented-heading", "indented-code"])
def test_layout_variations_read_the_same(old, new):
    assert PAGE.count(old) == 1
    assert pricing.parse_pricing_page(PAGE.replace(old, new)) == pricing.parse_pricing_page(PAGE)


def test_a_section_ends_at_an_indented_heading():
    page = PAGE.replace("## Feature-specific pricing",
                        "  ## Cloud platform pricing\n\n" + MODEL_HEADER
                        + "| Claude Haiku 5 | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |"
                        + "\n\n## Feature-specific pricing")
    assert "claude-haiku-5" not in pricing.parse_pricing_page(page)


FAST_SECTION = "### Fast mode pricing\n\n| Model | Input | Output |\n| --- | --- | --- |\n"


@pytest.mark.parametrize("old, new, error", [
    ("### Fast mode pricing", "### Fast mode", "expected one heading, found 0"),
    ("## Feature-specific pricing",
     "## Cloud platform pricing\n\n" + FAST_SECTION
     + "| Claude Opus 5.5 | $8.80 USD / MTok | $44 USD / MTok |\n\n## Feature-specific pricing",
     "expected one heading, found 2"),
    ("| 5m cache writes | 1h cache writes |", "| 1h cache writes | 5m cache writes |",
     "unexpected table header"),
    ("| Output tokens |", "| Output |", "unexpected table header"),
    ("| Claude Opus 5 | $5 / MTok", "\n| Claude Opus 5 | $5 / MTok", "unexpected table header"),
    ("| :--- | :--- |\n", "| :--- | :-x- |\n", "unexpected table separator"),
    ("| --- | --- | --- |", "| --- | --- |", "unexpected table separator"),
    ("| $10 USD / MTok | $50 USD / MTok |", "| $10 USD / MTok | $50 USD / MTok | x |",
     "unexpected number of cells"),
    ("| Claude Opus 5 | $5 / MTok", "Claude Opus 5 | $5 / MTok", "without a leading pipe"),
    ("| Claude Opus 5 / Claude Opus 4.1 |", "Claude Opus 5 / Claude Opus 4.1 |",
     "without a leading pipe"),
    ("*footnote*", "```\n# example\n```", "code block"),
    ("*footnote*", "#### Older models", "sub-heading"),
    ("| $8 USD / MTok | $40 USD / MTok |", "| $8 USD per MTok | $40 USD per MTok |",
     "unreadable price"),
    ("| $8 USD / MTok | $40 USD / MTok |", "| from $8 USD / MTok | $40 USD / MTok |",
     "unreadable price"),
    ("| $2.50 / MTok |", "| TBD |", "unreadable price"),
    ("| $2.50 / MTok |", "| $1-$15 / MTok |", "unreadable price"),
    ("| $8 USD / MTok | $40 USD / MTok |", "| $40 USD / MTok | $8 USD / MTok |",
     "multipliers differ"),
    ("| $8 USD / MTok | $40 USD / MTok |", "| $8 USD / MTok | $60 USD / MTok |",
     "multipliers differ"),
    ("| Claude Opus 5 / Claude Opus 4.1 |", "| Claude Opus 5 and Claude Opus 4.1 |",
     "unknown fast mode model"),
    ("| Claude Opus 5 / Claude Opus 4.1 |", "| Claude Opus 5 / Claude Opus 9 |",
     "unknown fast mode model"),
    ("| Claude Opus 5 / Claude Opus 4.1 |", "| Claude Opus 5 / Claude Opus 4.1 / Claude Opus 5.5 |",
     "more than one fast mode row"),
    ("| Claude Opus 5 | $5 / MTok", "| Claude Opus 5 | $0 / MTok", "no standard price"),
    ("| Claude Opus 5 | $5 / MTok", "| Claude Sonnet 5 (for prompts above 1M tokens) | $5 / MTok",
     "more than one row"),
    ("| Claude Opus 5 |", "| Claude Opus 5 (for prompts over 200,000 tokens) |",
     "only a long-prompt tier"),
], ids=["fast-heading", "fast-heading-twice", "model-columns-swapped", "model-column-renamed",
        "table-without-header", "separator-cell", "separator-length", "row-length",
        "model-row-without-pipe", "fast-row-without-pipe",
        "code-block", "sub-heading", "fast-price", "fast-price-prefix", "model-price", "model-price-range",
        "fast-columns-swapped", "fast-output-ratio", "fast-names-joined-with-and",
        "fast-unknown-model", "fast-duplicate-model", "zero-standard-price", "model-duplicate", "long-prompt-tier-only"])
def test_a_changed_page_layout_fails_instead_of_shipping(old, new, error):
    assert PAGE.count(old) == 1
    with pytest.raises(ValueError, match=error):
        pricing.parse_pricing_page(PAGE.replace(old, new))


TIERED = """
## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.40 / MTok | $20 / MTok |
{rows}

## Feature-specific pricing

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Opus 5.5 | $8 USD / MTok | $40 USD / MTok |
"""
UP_TO = "| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |"
OVER = "| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |"


@pytest.mark.parametrize("rows", [[UP_TO, OVER], [OVER, UP_TO]])
def test_prompt_length_tiers_use_the_standard_tier(rows):
    # The bundled table has one price per model, so the tier most requests fall into wins.
    models = pricing.parse_pricing_page(TIERED.format(rows="\n".join(rows)))
    assert models["claude-haiku-5-5"] == {
        "input": 0.1, "cache_write_5m": 0.125, "cache_write_1h": 0.2,
        "cache_read": 0.01, "output": 0.5,
    }


NESTED_NOTES = """
## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 4.1 ([retired, except on Bedrock and Claude Models on Google Cloud (Google managed)](https://x/y)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Mythos 5.1 ([limited availability](https://x/z)) | $10 / MTok | $12.50 / MTok | $20 / MTok | $1 / MTok | $50 / MTok |

## Feature-specific pricing

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Mythos 5.1 | $20 USD / MTok | $100 USD / MTok |
"""


def test_notes_with_nested_parentheses_are_dropped_from_the_name():
    models = pricing.parse_pricing_page(NESTED_NOTES)
    assert set(models) == {"claude-opus-4-1", "claude-mythos-5-1"}


def test_unexpected_model_names_fail_instead_of_shipping():
    page = NESTED_NOTES.replace("Claude Mythos 5.1", "Claude Mythos 5.1 ]")
    with pytest.raises(ValueError, match="unexpected model names"):
        pricing.parse_pricing_page(page)


def test_bundled_model_names_look_like_model_ids():
    for model in pricing.load_bundled():
        assert pricing._MODEL_ID.match(model), model


def test_parse_fails_loudly_on_unexpected_layout():
    with pytest.raises(ValueError, match="expected one heading"):
        pricing.parse_pricing_page("# Pricing\n\nnothing here")


def test_bundled_table_covers_current_models():
    prices = pricing.load_bundled()
    for model in ("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"):
        assert set(pricing.FIELDS) <= set(prices[model])


def test_bundled_cache_reads_are_cheaper_than_input():
    # Catches a wrong tier or column picked up by the parser before it ships.
    for model, p in pricing.load_bundled().items():
        assert p["cache_read"] < p["input"] < p["output"], model
        assert p["cache_write_5m"] < p["cache_write_1h"], model


def test_cost_uses_every_token_type():
    prices = {"m": {"input": 1, "cache_write_5m": 2, "cache_write_1h": 3,
                    "cache_read": 4, "output": 5}}
    tokens = {"input": 1e6, "cache_write_5m": 1e6, "cache_write_1h": 1e6,
              "cache_read": 1e6, "output": 1e6}
    assert pricing.cost(prices, "m", "standard", tokens) == 15


def test_cost_strips_date_suffix_and_applies_fast_multiplier():
    prices = {"claude-x": {"input": 1, "cache_write_5m": 0, "cache_write_1h": 0,
                           "cache_read": 0, "output": 0, "fast_multiplier": 2}}
    tokens = {"input": 1e6}
    assert pricing.cost(prices, "claude-x-20251001", "standard", tokens) == 1
    assert pricing.cost(prices, "claude-x", "fast", tokens) == 2


def test_unknown_model_has_no_cost():
    assert pricing.cost({}, "claude-unknown", "standard", {"input": 1}) is None


def test_overrides_merge_onto_bundled():
    prices = pricing.load({"claude-sonnet-5": {"output": 99},
                           "claude-new": {"input": 1}})
    assert prices["claude-sonnet-5"]["output"] == 99
    assert prices["claude-sonnet-5"]["input"] == 2.0
    assert prices["claude-new"] == {"input": 1}


OVERRIDE = {"input": 3, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.3,
            "output": 15, "fast_multiplier": 2}
WRONG_PRICES = [v for v in BAD_VALUES if not (type(v) in (int, float) and v in (1, 1.5))]


def test_valid_overrides_replace_or_add_prices():
    prices = pricing.load({"claude-new": OVERRIDE, "claude-sonnet-5": {"output": 20},
                           "claude-free": {**OVERRIDE, "input": -0.0}})
    assert pricing.rates(prices, "claude-new", "fast")["input"] == 6 / 1e6
    assert pricing.rates(prices, "claude-sonnet-5", "standard")["output"] == 20 / 1e6
    assert str(prices["claude-free"]["input"]) == "0.0"


def test_a_wrong_price_makes_that_model_unknown_instead_of_failing():
    for field in OVERRIDE:
        for bad in WRONG_PRICES:
            for model in ("claude-new", "claude-sonnet-5"):
                prices = pricing.load({model: {**OVERRIDE, field: bad}, "claude-new-2": OVERRIDE})
                assert pricing.cost(prices, model, "standard", {"input": 1}) is None
                assert pricing.rates(prices, "claude-new-2", "standard") is not None
    for bad in BAD_VALUES:
        if not isinstance(bad, dict):
            assert pricing.rates(pricing.load({"claude-sonnet-5": bad}), "claude-sonnet-5",
                                 "standard") is None


def test_overrides_that_are_not_an_object_are_ignored():
    for bad in BAD_VALUES:
        assert pricing.load(bad if bad != {"a": 1} else {}) == pricing.load_bundled()
