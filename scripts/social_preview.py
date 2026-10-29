"""Render assets/social-preview.png (1280x640), the image GitHub shows for shared links.

Needs Pillow and macOS system fonts (Menlo, Helvetica Neue); not used at runtime.
    python scripts/social_preview.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (40, 44, 52)
PANEL = (30, 33, 39)
BORDER = (70, 76, 88)
TEXT = (230, 233, 239)
MUTED = (150, 156, 168)
GRAY = (160, 160, 160)
CYAN = (125, 196, 210)
MAGENTA = (190, 160, 210)
YELLOW = (229, 192, 123)
DIM_YELLOW = (160, 135, 90)

MONO = "/System/Library/Fonts/Menlo.ttc"
SANS = "/System/Library/Fonts/HelveticaNeue.ttc"

# the status line, split into colored runs as tallyline prints it
STATUS = [
    ("[Opus 5.5]", GRAY), (" │ ", BORDER), ("ctx 165k (16%)", CYAN), (" │ ", BORDER),
    ("5h 25%  7d 12%", MAGENTA), (" │ ", BORDER), ("Oct 1.1B tok ", YELLOW),
    ("(≈$504.97)", DIM_YELLOW),
]


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(SANS, 104, index=1)  # bold
    tagline = ImageFont.truetype(SANS, 38)
    status = ImageFont.truetype(MONO, 25)
    install = ImageFont.truetype(MONO, 28)

    x = 96
    d.text((x, 96), "tallyline", font=title, fill=TEXT)
    d.text((x, 226), "Token usage and cost for Claude Code, at a glance", font=tagline, fill=MUTED)

    # status line in a terminal-like panel, aligned with the text above
    text = "".join(t for t, _ in STATUS)
    width = d.textlength(text, font=status)
    top, height = 316, 88
    d.rounded_rectangle([x, top, W - x, top + height], radius=14, fill=PANEL,
                        outline=BORDER, width=2)
    cx, cy = (W - width) / 2, top + height / 2
    for t, color in STATUS:
        d.text((cx, cy), t, font=status, fill=color, anchor="lm")
        cx += d.textlength(t, font=status)

    d.text((x, 462), "$ pipx install tallyline && tallyline init", font=install, fill=MUTED)

    out = Path(__file__).resolve().parent.parent / "assets" / "social-preview.png"
    img.save(out, optimize=True)
    print(out)


if __name__ == "__main__":
    main()
