"""Generate deterministic SYNTHETIC fixtures, never presented as manga validation.

Run: python examples/make_demo.py [destination]
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import plistlib
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, TILE_HEIGHT, HEIGHT = 690, 1280, 10240
SCENES = [
    {"name": "single_source_with_bubble", "bounds": [45, 120, 645, 900], "label": "SYNTHETIC / COMPLETE SCENE + BUBBLE"},
    {"name": "two_source_continuation", "bounds": [20, 1120, 670, 2310], "label": "SYNTHETIC / TWO STRIP SCENE"},
    {"name": "tall_four_source_continuation", "bounds": [30, 2700, 660, 6470], "label": "SYNTHETIC / TALL FOUR STRIP SCENE"},
    {"name": "gutter_scene_one", "bounds": [90, 6750, 600, 7250], "label": "SYNTHETIC / SEPARATE SCENE A"},
    {"name": "gutter_scene_two", "bounds": [50, 7450, 640, 7950], "label": "SYNTHETIC / SEPARATE SCENE B"},
    {"name": "small_panel", "bounds": [240, 8250, 450, 8460], "label": "SYNTHETIC / SMALL"},
    {"name": "speech_bubble_scene", "bounds": [40, 8850, 650, 9710], "label": "SYNTHETIC / KEEP SPEECH BUBBLE"},
]


def font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_scene(chapter: Image.Image, item: dict, index: int):
    x0, y0, x1, y1 = item["bounds"]
    width, height = x1 - x0, y1 - y0
    yy, xx = np.mgrid[:height, :width]
    base = np.empty((height, width, 3), np.uint8)
    base[:, :, 0] = (35 + (xx * 73 // width) + index * 13) % 200
    base[:, :, 1] = 65 + yy * 100 // height
    base[:, :, 2] = 110 + ((xx + yy) % 89)
    panel = Image.fromarray(base, "RGB")
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(22, 28, 44), width=8)
    # Art reaches both sides and continues through source-strip boundaries.
    for offset in range(80, height, 180):
        draw.line((14, offset, width - 15, min(height - 15, offset + 100)), fill=(68, 183, 165), width=5)
    cx, cy = width // 2, min(height // 2, 420)
    body_h = min(max(height // 3, 80), 540)
    draw.ellipse((cx - width // 7, cy - 65, cx + width // 7, cy + 65), fill=(233, 192, 149), outline=(24, 23, 35), width=5)
    draw.polygon([(cx - 55, cy + 30), (cx + 60, cy + 35), (cx + 100, cy + body_h), (cx - 105, cy + body_h)], fill=(104, 54, 122), outline=(22, 20, 34), width=5)
    # A deliberately long weapon and limbs must stay inside the natural crop.
    draw.line((max(12, cx - 160), cy + 100, min(width - 13, cx + 200), min(height - 15, cy + 200)), fill=(232, 214, 138), width=12)
    draw.line((cx - 35, cy + body_h, max(15, cx - 130), height - 18), fill=(32, 34, 49), width=18)
    draw.line((cx + 35, cy + body_h, min(width - 16, cx + 130), height - 18), fill=(32, 34, 49), width=18)
    text_size = 17 if width > 400 else 11
    draw.rectangle((10, 10, width - 11, 48), fill=(22, 28, 44))
    draw.text((17, 19), item["label"], fill="white", font=font(text_size))
    if "bubble" in item["name"]:
        bx0, by0 = 20, 75
        bx1, by1 = min(width - 20, 260), 200
        draw.ellipse((bx0, by0, bx1, by1), fill="white", outline=(18, 22, 33), width=4)
        draw.polygon([(bx1 - 65, by1 - 12), (bx1 - 20, by1 + 35), (bx1 - 30, by1 - 35)], fill="white")
        draw.text((bx0 + 27, by0 + 34), "KEEP THIS\nSPEECH BUBBLE", fill=(20, 20, 25), font=font(15))
    chapter.paste(panel, (x0, y0))


def make_demo(destination: str | Path) -> dict:
    dest = Path(destination)
    images = dest / "input"
    images.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
    for i, item in enumerate(SCENES):
        draw_scene(canvas, item, i)
    canvas.save(dest / "synthetic_chapter.png")
    resources, html = [], ["<html><body><div id='comic_viewer'>"]
    with zipfile.ZipFile(dest / "synthetic_chapter.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for i, y in enumerate(range(0, HEIGHT, TILE_HEIGHT), 1):
            name = f"SYN_{i:03d}.png"
            path = images / name
            canvas.crop((0, y, WIDTH, min(y + TILE_HEIGHT, HEIGHT))).save(path)
            payload = path.read_bytes()
            archive.writestr("chapter/" + name, payload)
            url = f"https://synthetic.invalid/chapter/{name}"
            resources.append({"WebResourceURL": url, "WebResourceMIMEType": "image/png", "WebResourceData": payload})
            html.append(f'<img class="_images" src="{url}" alt="Synthetic fixture strip {i}">')
        icon = Image.new("RGB", (24, 24), "red")
        icon_data = io.BytesIO()
        icon.save(icon_data, format="PNG")
        archive.writestr("assets/icon.png", icon_data.getvalue())
    html.append("</div></body></html>")
    archive_data = {
        "WebMainResource": {"WebResourceURL": "https://synthetic.invalid/chapter", "WebResourceMIMEType": "text/html", "WebResourceData": "".join(html).encode()},
        "WebSubresources": resources,
    }
    with (dest / "synthetic_chapter.webarchive").open("wb") as handle:
        plistlib.dump(archive_data, handle, fmt=plistlib.FMT_BINARY)
    expected = {"synthetic": True, "width": WIDTH, "height": HEIGHT, "tile_height": TILE_HEIGHT, "scenes": SCENES, "white_gutters": [[900, 1120], [2310, 2700], [6470, 6750], [7250, 7450], [7950, 8250], [8460, 8850]], "note": "These fixtures test geometry and pixel preservation. They do not validate any Leviathan artwork."}
    (dest / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default="examples/demo")
    args = parser.parse_args()
    info = make_demo(args.destination)
    print(f"Created SYNTHETIC fixtures in {args.destination}: {len(info['scenes'])} scenes, 8 source strips.")
