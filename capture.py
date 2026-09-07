"""Capture the demo's real output as terminal-styled images.

This runs the actual demo — same code paths, same policy engine, same
verdicts — through a recording console, and writes one image per act.
The images are a record of a real run, not a mockup: if the policy changes,
re-running this changes the pictures.

    python capture.py
"""

from __future__ import annotations

import shutil
import subprocess
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

from rich.console import Console  # noqa: E402

import theatre  # noqa: E402

OUT = Path(__file__).parent / "docs" / "images"

# Swap in a recording console before the demo runs. theatre's helpers look up
# the module global at call time, so demo.py picks this up without knowing.
theatre.console = Console(record=True, width=98)

import demo  # noqa: E402


def _save(slug: str, title: str) -> Path:
    path = OUT / f"{slug}.svg"
    theatre.console.save_svg(str(path), title=title, clear=True)
    return path


def _render_png(svg: Path) -> bool:
    """Rasterise an SVG to a tightly cropped PNG.

    Uses macOS Quick Look plus Pillow. Both are optional: the SVGs are the
    portable artefact, and this step is a convenience for README embedding,
    since GitHub renders PNG more reliably than SVG.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False
    if not shutil.which("qlmanage"):
        return False

    subprocess.run(
        ["qlmanage", "-t", "-s", "1800", "-o", str(svg.parent), str(svg)],
        check=True, capture_output=True,
    )
    raw = svg.parent / f"{svg.name}.png"
    if not raw.exists():
        return False

    # Quick Look pads the render into a square canvas; crop back to content.
    im = Image.open(raw).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if bbox:
        pad = 8
        im = im.crop((
            max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
            min(bbox[2] + pad, im.width), min(bbox[3] + pad, im.height),
        ))
    im.save(svg.with_suffix(".png"))
    raw.unlink()
    return True


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    acts = [
        ("01-ungoverned", "Act 1 — the agent you have today", lambda: demo.act_one()),
    ]
    for slug, title, run in acts:
        run()
        written.append(_save(slug, title))

    # Act 2 hands the governed callable to acts 3 and 6.
    (safe_db,) = demo.act_two()
    written.append(_save("02-governed", "Act 2 — two lines of governance"))

    for slug, title, run in [
        ("03-sql-ast", "Act 3 — structure, not string matching", lambda: demo.act_three(safe_db)),
        ("04-approval", "Act 4 — a human in the loop", demo.act_four),
        ("05-identity", "Act 5 — which agent did this?", demo.act_five),
        ("06-audit", "Act 6 — can you prove what happened?", lambda: demo.act_six(safe_db)),
    ]:
        run()
        written.append(_save(slug, title))

    here = Path(__file__).parent
    for svg in written:
        png = _render_png(svg)
        print(f"wrote {svg.relative_to(here)}" + (f" and {svg.with_suffix('.png').relative_to(here)}" if png else ""))


if __name__ == "__main__":
    main()
