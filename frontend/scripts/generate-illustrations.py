"""
Generate FarmOS Modern Agritech illustrations via OpenAI gpt-image-2.
Writes PNGs to frontend/public/illustrations/.

Usage:
    OPENAI_API_KEY=sk-... python frontend/scripts/generate-illustrations.py [name1 name2 ...]

If names are omitted, generates all. Names: hero | journal-empty | onboarding-complete

This script is intentionally not committed in any form that contains the key.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

API_URL = "https://api.openai.com/v1/images/generations"
# gpt-image-2 requires org verification; gpt-image-1 is a clean fallback that
# this account already has access to. Override with FARMOS_IMAGE_MODEL env var.
MODEL = os.environ.get("FARMOS_IMAGE_MODEL", "gpt-image-1")

PALETTE = (
    "color palette strictly limited to: leaf green #2E7D52, soft sage #8AAE93, "
    "warm linen cream #F7F6F1, muted ochre, slate grey, soft persimmon orange "
    "used sparingly. no neon, no purple, no harsh blacks."
)

STYLE = (
    "modern editorial illustration in soft gouache-and-ink style with a subtle "
    "hand-drawn paper grain. flat shapes with gentle washes. confident clean "
    "outlines, mostly negative space, calm and professional, no gradients, "
    "no photorealism, no 3D rendering, no text or letters anywhere in the image."
)

MODULE_BASE = (
    "A small square painted illustration suitable for a UI category card. "
    "Single iconic subject, centered, generous warm cream negative space all "
    "around (about 25% margin). No text, no logos, no characters, no people, "
    "no hands. "
)

JOBS = {
    # ── Module category illustrations (dashboard "둘러보기" cards) ──
    "module-iot": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A simple plant-pot-shaped weather/soil sensor "
            "stake planted in a small mound of soil with two tender green "
            "leaves curling around its stem. A faint wave of signal lines "
            "ripples gently from its top, suggesting connectivity. Soft "
            "morning light from upper right. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-diagnosis": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A single broad green leaf seen from above, with a "
            "rounded magnifying glass hovering over it. Through the glass, "
            "the leaf detail looks slightly more saturated. A tiny blemish "
            "or spot on the leaf hints at the diagnostic purpose. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-journal": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A small worn cloth-bound farmer's journal lying "
            "open with a fountain pen resting diagonally across the right "
            "page. Two pressed leaves tucked against the binding. Nothing "
            "written on the pages. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-weather": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A soft puffy cloud with three gentle rain droplets "
            "falling beneath, and a partial pale sun peeking from behind the "
            "upper-right edge of the cloud. Calm, friendly, balanced. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-market": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A traditional brass-and-wood balance scale with two "
            "hanging dishes; one dish holds a small red apple, the other a "
            "few coins. The scale's bar is gently uneven, suggesting price "
            "tipping. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-subsidy": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A simple hanok-style government document scroll "
            "partially unrolled, with a small wax seal in soft persimmon "
            "orange near the bottom edge. A single bound thread tied at the "
            "side. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "module-reviews": {
        "size": "1024x1024",
        "prompt": (
            f"{MODULE_BASE}A small chat speech bubble shape made of soft "
            "paper, with a single five-pointed star resting at its center. "
            "Two tiny thinner bubbles trail behind to suggest conversation. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "hero": {
        "size": "1536x1024",
        "prompt": (
            "A serene minimalist illustration of an early-morning Korean farm "
            "valley. Gentle rolling hills with terraced rice paddies catching "
            "pale sunlight, a single mature persimmon tree on the right with two "
            "or three ripe orange fruits, a small wooden farmhouse with a low "
            "grey-tile hanok-style roof tucked into the middle distance. A pale "
            "warm sun rises behind soft mist; thin morning haze sits in the "
            "valley. Generous empty cream sky in the upper third for layout "
            "breathing room. No people, no animals, no text, no signs. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "journal-empty": {
        "size": "1024x1024",
        "prompt": (
            "A warm minimalist illustration: an open empty notebook with two "
            "blank pages lying flat, viewed from a gentle three-quarter angle. "
            "From the right page, a single tender green sprout with two leaves "
            "grows upward as if drawn into life. A small earthenware tea cup "
            "sits beside the notebook with thin steam curling. Subtle paper "
            "texture on the desk surface, lots of negative space around the "
            "subject. No characters, no hands, no faces, no text. "
            f"{STYLE} {PALETTE}"
        ),
    },
    "onboarding-complete": {
        "size": "1024x1024",
        "prompt": (
            "A serene illustration of a single tender green seedling with two "
            "small leaves growing from a soft mound of rich brown earth. Soft "
            "warm morning light falls from the upper right, casting a gentle "
            "long shadow. The mound sits centered with very generous empty "
            "cream space all around. Tiny stylized particles of soil or pollen "
            "drift gently in the air. No hands, no characters, no text, no "
            "logos. Calm, hopeful, ceremonial. "
            f"{STYLE} {PALETTE}"
        ),
    },
}

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "illustrations"


def generate(name: str, key: str) -> Path:
    job = JOBS[name]
    body = json.dumps({
        "model": MODEL,
        "prompt": job["prompt"],
        "size": job["size"],
        "quality": "high",
        "n": 1,
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    item = payload["data"][0]
    b64 = item.get("b64_json")
    if not b64:
        raise RuntimeError(f"no b64_json in response for {name}: {item.keys()}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.png"
    out.write_bytes(base64.b64decode(b64))
    return out


def main() -> int:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY env var required", file=sys.stderr)
        return 1

    targets = sys.argv[1:] or list(JOBS)
    for name in targets:
        if name not in JOBS:
            print(f"unknown job: {name}", file=sys.stderr)
            return 2
        print(f"→ generating {name} ({JOBS[name]['size']})...", flush=True)
        try:
            out = generate(name, key)
            kb = out.stat().st_size // 1024
            print(f"  saved {out.relative_to(out.parent.parent.parent)} ({kb} KB)")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")[:600]
            print(f"  HTTP {e.code}: {err}", file=sys.stderr)
            return 3
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
