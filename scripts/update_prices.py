"""Regenerate src/tallyline/prices.json from Anthropic's official pricing page.

Run by the update-prices GitHub Actions workflow; exits non-zero if parsing fails
so a broken page layout never ships an empty table.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tallyline.pricing import parse_pricing_page  # noqa: E402

SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing.md"
OUT = Path(__file__).resolve().parents[1] / "src" / "tallyline" / "prices.json"


def main():
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "tallyline-price-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        markdown = r.read().decode("utf-8")
    models = parse_pricing_page(markdown)
    data = {"source": SOURCE, "unit": "USD per million tokens",
            "models": dict(sorted(models.items()))}
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(models)} models to {OUT}")


if __name__ == "__main__":
    main()
