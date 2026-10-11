"""Find things in a Shot: Apple Vision OCR (Japanese + English) and colour blobs.

Vision replaces mcp-vroid's tesseract: it reads VRoid's Japanese UI, runs
on-device in well under a second for a full window, and handles white-on-blue
button labels that tesseract drops.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import Quartz
import Vision
from Foundation import NSData
from PIL import Image

from .capture import Shot


@dataclass
class Match:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    _sub: object = field(default=None, repr=False, compare=False)  # callable(start, length) -> Match for a substring

    def sub(self, start: int, length: int) -> "Match":
        """Exact box of text[start:start+length] (Vision per-character boxes)."""
        if self._sub is not None:
            try:
                m = self._sub(start, length)
                if m is not None:
                    return m
            except Exception:
                pass
        n = max(1, len(self.text))
        return Match(self.text[start:start + length], self.conf,
                     int(self.left + self.width * start / n), self.top,
                     max(4, int(self.width * length / n)), self.height)

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def as_dict(self) -> dict:
        return {"text": self.text, "conf": round(self.conf, 2), "center": self.center,
                "box": [self.left, self.top, self.width, self.height]}


def _cgimage(img: Image.Image):
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def ocr(img: Image.Image | Shot, region: tuple | None = None,
        langs=("ja-JP", "en-US"), min_conf: float = 0.3, fast: bool = False) -> list[Match]:
    """Text lines with boxes in image px of the *original* image."""
    src = img.image if isinstance(img, Shot) else img
    off = (0, 0)
    if region:
        region = tuple(int(v) for v in region)
        src = src.crop(region)
        off = region[:2]
    W, H = src.size
    # Vision is weak on tiny UI text; upscale small crops.
    up = 2.0 if max(W, H) < 1200 else 1.0
    work = src.resize((int(W * up), int(H * up)), Image.LANCZOS) if up != 1.0 else src
    req = Vision.VNRecognizeTextRequest.alloc().init()
    # NB: the Fast level does not support Japanese, and asking it for ja-JP
    # aborts the process inside Vision (Swift array fatal error). Accurate only.
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(list(langs))
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(_cgimage(work), None)
    ok, err = handler.performRequests_error_([req], None)
    out: list[Match] = []
    if not ok:
        return out
    for r in req.results() or []:
        cand = r.topCandidates_(1)
        if not cand:
            continue
        c = cand[0]
        if c.confidence() < min_conf:
            continue
        bb = r.boundingBox()  # normalised, origin bottom-left
        x = bb.origin.x * W
        y = (1 - bb.origin.y - bb.size.height) * H
        out.append(Match(str(c.string()), float(c.confidence()),
                         int(x + off[0]), int(y + off[1]),
                         int(bb.size.width * W), int(bb.size.height * H),
                         _make_sub(c, W, H, off)))
    out.sort(key=lambda m: (m.top, m.left))
    return out


def _make_sub(cand, W, H, off):
    def sub(start: int, length: int):
        obs, err = cand.boundingBoxForRange_error_((start, length), None)
        if obs is None:
            return None
        bb = obs.boundingBox()
        return Match(str(cand.string())[start:start + length], float(cand.confidence()),
                     int(bb.origin.x * W + off[0]),
                     int((1 - bb.origin.y - bb.size.height) * H + off[1]),
                     int(bb.size.width * W), int(bb.size.height * H))
    return sub


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s・:：()（）\[\]「」『』、。,.!?！？/\\_\-–—~〜>＞<＜|]", "", s)


def _narrow(m: Match, w: str) -> Match:
    """Box of the normalised needle `w` inside line `m`."""
    raw = m.text
    # map each normalised char back to its index in the raw string
    idx = [i for i, ch in enumerate(raw) if norm(ch)]
    t = "".join(norm(raw[i]) for i in idx)
    j = t.find(w)
    if j < 0 or len(idx) != len(t):
        return m
    start, end = idx[j], idx[j + len(w) - 1] + 1
    return m.sub(start, end - start)


def find_text(img: Image.Image | Shot, needle: str | list[str], *, exact: bool = False,
              all_matches: bool = False, region: tuple | None = None,
              lines: list[Match] | None = None):
    """Best (or all) OCR lines matching any of `needle` (ja/en synonyms).

    Matching is on normalised text; non-exact matching is substring either
    way so 'カスタマイズ' finds 'カスタマイズ >' and vice versa.
    For multi-word lines the box is narrowed proportionally to the needle's
    position inside the line, so clicking .center hits the word.
    """
    needles = [needle] if isinstance(needle, str) else list(needle)
    wants = [norm(n) for n in needles if norm(n)]
    lines = lines if lines is not None else ocr(img, region=region)
    hits: list[tuple[float, Match]] = []
    for m in lines:
        t = norm(m.text)
        if not t:
            continue
        for w in wants:
            if exact:
                if t != w:
                    continue
                score = 2.0
            elif w in t:
                score = 1.0 + len(w) / len(t)
                if len(t) > len(w):
                    m = _narrow(m, w)
            elif len(t) >= 2 and t in w:
                score = len(t) / len(w)
                if score < 0.6:
                    continue
            else:
                continue
            hits.append((score + m.conf * 0.1, m))
            break
    hits.sort(key=lambda h: -h[0])
    found = [m for _, m in hits]
    if all_matches:
        return found
    return found[0] if found else None


# --- colour --------------------------------------------------------------------

ACCENT = (0, 150, 250)   # VRoid primary blue #0096FA (measured on Linux; re-check on mac)


def color_blobs(img: Image.Image | Shot, rgb=ACCENT, tol: int = 30, min_w: int = 30,
                min_h: int = 12, region: tuple | None = None) -> list[Match]:
    import cv2
    src = img.image if isinstance(img, Shot) else img
    off = (0, 0)
    if region:
        region = tuple(int(v) for v in region)
        src = src.crop(region)
        off = region[:2]
    a = np.asarray(src.convert("RGB")).astype(np.int16)
    mask = (np.abs(a - np.array(rgb, dtype=np.int16)).max(axis=2) <= tol).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < min_w or h < min_h or area < 0.4 * w * h:
            continue
        out.append(Match(f"<blob #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}>", 1.0,
                         int(x + off[0]), int(y + off[1]), int(w), int(h)))
    out.sort(key=lambda m: -(m.width * m.height))
    return out


def pixel(img: Image.Image | Shot, x: int, y: int) -> str:
    src = img.image if isinstance(img, Shot) else img
    r, g, b = src.getpixel((int(x), int(y)))[:3]
    return f"#{r:02X}{g:02X}{b:02X}"


def ui_diff(a: Shot, b: Shot, box: tuple | None = None) -> float:
    """Fraction of changed pixels in `box` (use a UI strip, not the 3D view -
    the viewport dithers every frame)."""
    ia, ib = a.image, b.image
    if ia.size != ib.size:
        ib = ib.resize(ia.size)
    if box:
        ia, ib = ia.crop(box), ib.crop(box)
    na = np.asarray(ia.convert("L"), dtype=np.int16)
    nb = np.asarray(ib.convert("L"), dtype=np.int16)
    return float((np.abs(na - nb) > 12).mean())
