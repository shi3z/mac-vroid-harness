"""Screenshots and the three coordinate spaces.

    image px   pixels of a full-resolution window capture (Retina: 2x points).
               OCR, colour matching and every tool's default `space`.
    shown px   pixels of the downscaled copy sent to the MCP client.
               shown = image * downscale.
    points     CoreGraphics global coordinates, what CGEvent consumes.
               point = window_origin + image / scale.

A Shot remembers its window origin and scale, so any pixel in it can be
converted to a click point even if the window moves afterwards (re-shoot
first in that case - guard() does).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import macos as M
from . import session as S

MAX_SHOWN_PX = int(os.environ.get("VROID_HARNESS_MAX_IMAGE_PX", "1568"))


@dataclass
class Shot:
    image: Image.Image
    path: Path
    origin: tuple[float, float]       # global points of image px (0,0)
    scale: float                      # image px per point
    kind: str = "window"              # window | screen | region
    meta: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    def to_points(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin[0] + x / self.scale, self.origin[1] + y / self.scale)

    def from_points(self, px: float, py: float) -> tuple[float, float]:
        return ((px - self.origin[0]) * self.scale, (py - self.origin[1]) * self.scale)

    def crop(self, box) -> Image.Image:
        return self.image.crop(tuple(int(v) for v in box))


_last: dict[str, Shot] = {}


def last(kind: str = "window") -> Shot | None:
    return _last.get(kind)


def _screencapture(args: list[str], out: Path) -> None:
    r = subprocess.run(["screencapture", "-x", *args, str(out)], capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        msg = (r.stderr or r.stdout).strip()
        if not M.permissions()["screen_recording"]:
            msg += (" | Screen Recording permission missing for the host terminal: System "
                    "Settings > Privacy & Security > Screen & System Audio Recording.")
        raise RuntimeError(f"screencapture failed: {msg}")


def grab_window(tag: str = "") -> Shot:
    w = M.main_window()
    if w is None:
        raise RuntimeError("VRoid Studio window not found (is it running and not minimised?)")
    path = S.capture_path(tag or "window")
    _screencapture(["-o", "-l", str(w.wid)], path)
    img = Image.open(path).convert("RGB")
    scale = img.width / w.w if w.w else 1.0
    s = Shot(img, path, (w.x, w.y), scale, "window",
             {"window": w.bounds, "wid": w.wid})
    _last["window"] = s
    return s


def grab_rect(x: float, y: float, w: float, h: float, tag: str = "") -> Shot:
    """Capture a rectangle in global points (e.g. a save panel)."""
    path = S.capture_path(tag or "rect")
    _screencapture(["-R", f"{int(x)},{int(y)},{int(w)},{int(h)}"], path)
    img = Image.open(path).convert("RGB")
    s = Shot(img, path, (x, y), img.width / w if w else 1.0, "region")
    _last["region"] = s
    return s


def grab_panel(tag: str = "panel") -> Shot | None:
    p = M.panel_window()
    if p is None:
        return None
    path = S.capture_path(tag)
    _screencapture(["-o", "-l", str(p.wid)], path)
    img = Image.open(path).convert("RGB")
    s = Shot(img, path, (p.x, p.y), img.width / p.w if p.w else 1.0, "panel",
             {"window": p.bounds, "wid": p.wid})
    _last["panel"] = s
    return s


def for_transport(img: Image.Image, max_px: int | None = None) -> tuple[bytes, float]:
    """PNG bytes no larger than max_px on the long edge, plus the factor
    applied (shown = image * factor)."""
    max_px = MAX_SHOWN_PX if max_px is None else max_px
    f = 1.0
    if max_px and max(img.size) > max_px:
        f = max_px / max(img.size)
        img = img.resize((round(img.width * f), round(img.height * f)), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png") as t:
        img.save(t.name, optimize=True)
        return Path(t.name).read_bytes(), f


def zoom(s: Shot, box: tuple[int, int, int, int], factor: float = 2.0) -> Image.Image:
    """Crop `box` (image px) and enlarge it for detailed inspection."""
    c = s.crop(box)
    if factor != 1.0:
        c = c.resize((max(1, int(c.width * factor)), max(1, int(c.height * factor))), Image.LANCZOS)
    return c
