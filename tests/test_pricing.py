import pytest

from tallyline import pricing

PAGE = """
## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock](https://x/y)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Sonnet 5 | $2 / MTok<sup>3</sup> | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok<sup>3</sup> |

*footnote*

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Opus 5.5 | $8 / MTok | $40 / MTok |
"""


def test_parse_pricing_page():
    models = pricing.parse_pricing_page(PAGE)
    assert set(models) == {"claude-opus-5-5", "claude-opus-4-1", "claude-sonnet-5"}
    assert models["claude-opus-5-5"] == {
        "input": 4.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0,
        "cache_read": 0.2, "output": 20.0, "fast_multiplier": 2.0,
    }
    assert models["claude-sonnet-5"]["output"] == 10.0
    assert "fast_multiplier" not in models["claude-sonnet-5"]


TIERED = """
## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :--- | :--- | :--- | :--- | :--- | :--- |
{rows}
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


def test_parse_fails_loudly_on_unexpected_layout():
    with pytest.raises(ValueError):
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
