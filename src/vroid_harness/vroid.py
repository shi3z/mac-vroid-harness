"""VRoid Studio flows (2.14.0, macOS, Japanese or English UI).

Same shape as mcp-vroid's actions.py: act -> screenshot -> assert on what the
screenshot says. Locating is OCR first (labels in ja + en), measured
fractions of the window only for icons that carry no text.

Geometry notes measured on a 3456x2168 image px window (1728x1084 pt, 2x):
  tab strip            y = 0.047 H
  left icon rail       x = 0.014 W, first icon y = 0.096 H, pitch ~0.0256 H
  left panel title     (0.022..0.10 W, 0.08..0.11 H)   e.g. 顔セット
  preset grid          x 0.025..0.16 W
  right Parameters     x 0.83..0.995 W, numeric box at x ~0.97 W on the label row
  toolbar icons        camera 0.917 W, share/export 0.944 W, kebab 0.972 W
  hamburger            (0.016 W, 0.047 H)
"""
from __future__ import annotations

import re
import unicodedata
import time
from pathlib import Path

from . import capture as C
from . import input as I
from . import locate as L
from . import macos as M
from . import session as S
from .capture import Shot

TABS = {
    "face": ["顔", "Face"],
    "hair": ["髪型", "Hairstyle"],
    "body": ["体型", "Body"],
    "outfit": ["衣装", "Outfit"],
    "accessories": ["アクセサリー", "Accessories"],
    "look": ["ルック", "Look"],
}
TAB_FALLBACK_X = {"face": 0.053, "hair": 0.092, "body": 0.139, "outfit": 0.185,
                  "accessories": 0.25, "look": 0.318}
TAB_Y = 0.047
RAIL_X = 0.014
RAIL_Y0 = 0.096
RAIL_PITCH = 0.0256
HAMBURGER = (0.016, 0.047)
TB_CAMERA = (0.917, 0.047)
TB_EXPORT = (0.944, 0.047)
TB_KEBAB = (0.972, 0.047)
PANEL_RIGHT = (0.825, 0.07, 0.998, 1.0)
PANEL_LEFT = (0.028, 0.07, 0.172, 1.0)
VIEWPORT = (0.172, 0.07, 0.825, 1.0)

NUM_RE = re.compile(r"^-?\d+([.,]\d+)?$")
HEX_RE = re.compile(r"#?([0-9A-Fa-f]{6})")


def shot(tag: str = "") -> Shot:
    return C.grab_window(tag)


def frac(s: Shot, fx: float, fy: float) -> tuple[int, int]:
    return int(s.image.width * fx), int(s.image.height * fy)


def box(s: Shot, b) -> tuple[int, int, int, int]:
    W, H = s.size
    return (int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H))


def wait_for(pred, timeout: float = 20.0, poll: float = 0.8, tag: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = shot(tag)
        v = pred(last)
        if v:
            return last, v
        time.sleep(poll)
    return last, None


def ensure_ready() -> Shot:
    """Running, window fully on a display, focused. Returns a fresh shot."""
    M.launch()
    if not M.window_on_screen():
        M.fit_window()
    if not M.activate():
        raise I.GuardError(f"could not bring VRoid Studio to the front (front: {M.front_owner()})")
    return shot("ready")


# --- screen identification --------------------------------------------------------

def current_screen(s: Shot | None = None) -> dict:
    """{'screen': editor|start|export|hair_editor|dialog|unknown, 'tab': ..., 'title': ...}"""
    s = s or shot("whereami")
    W, H = s.size
    top = L.ocr(s, region=(0, int(H * 0.025), W, int(H * 0.075)))
    toptext = " ".join(m.text for m in top)
    res = {"screen": "unknown", "tab": None, "panel_title": None, "top": toptext}
    tabs_seen = sum(1 for names in TABS.values() if any(L.norm(n) in L.norm(toptext) for n in names))
    if tabs_seen >= 4:
        res["screen"] = "editor"
        res["tab"] = active_tab(s)
        res["panel_title"] = panel_title(s)
    elif any(k in toptext for k in ("VRMエクスポート", "VRM Export", "Export as VRM", "VRM書き出し")):
        res["screen"] = "export"
    elif any(k in toptext for k in ("テクスチャを編集", "Edit Texture")):
        res["screen"] = "texture_editor"
    elif any(k in toptext for k in ("ヘアーエディター", "ヘアスタイルエディター", "Hairstyle Editor", "Edit Hair")):
        res["screen"] = "hair_editor"
    else:
        full = " ".join(m.text for m in L.ocr(s))
        if any(k in full for k in ("新規作成", "Create New", "最近編集", "Recently Edited")):
            res["screen"] = "start"
        res["text"] = full[:400]
    return res


def active_tab(s: Shot) -> str | None:
    """The tab whose label sits above the blue underline."""
    W, H = s.size
    y = int(H * 0.068)
    best, score = None, 0
    for key, names in TABS.items():
        m = L.find_text(s, names, region=(0, int(H * 0.03), int(W * 0.4), int(H * 0.068)))
        x = m.center[0] if m else int(W * TAB_FALLBACK_X[key])
        # count blue-ish pixels on the underline just below the label
        blue = 0
        for dx in range(-20, 21, 4):
            for dy in range(-6, 3, 2):
                r, g, b = s.image.getpixel((min(W - 1, x + dx), min(H - 1, y + dy)))[:3]
                if b > 180 and r < 120:
                    blue += 1
        if blue > score:
            best, score = key, blue
    return best if score >= 3 else None


def panel_title(s: Shot) -> str:
    W, H = s.size
    ms = L.ocr(s, region=(int(W * 0.028), int(H * 0.075), int(W * 0.17), int(H * 0.115)))
    return " ".join(m.text for m in ms)


# --- tabs / rail -------------------------------------------------------------------

def tab_key(name: str) -> str:
    n = L.norm(name)
    for k, names in TABS.items():
        if n == k or any(n == L.norm(x) for x in names):
            return k
    raise ValueError(f"unknown tab {name!r}; one of {list(TABS)} / {sum(TABS.values(), [])}")


def open_tab(name: str) -> Shot:
    k = tab_key(name)
    s = shot("before-tab")
    W, H = s.size
    m = L.find_text(s, TABS[k], region=(0, int(H * 0.03), int(W * 0.4), int(H * 0.068)))
    x, y = m.center if m else frac(s, TAB_FALLBACK_X[k], TAB_Y)
    I.click(x, y, shot=s)
    s2, ok = wait_for(lambda sh: active_tab(sh) == k, timeout=8, tag=f"tab-{k}")
    if not ok:
        raise RuntimeError(f"tab {name!r} did not become active; see {s2.path}")
    return s2


def rail_points(s: Shot, max_icons: int = 24) -> list[tuple[int, int]]:
    """Centres of the icons on the left rail, found by darkness profile."""
    import numpy as np
    W, H = s.size
    x0, x1 = int(W * 0.004), int(W * 0.025)
    strip = np.asarray(s.image.crop((x0, int(H * 0.075), x1, H)).convert("L"), dtype=np.int16)
    bg = np.median(strip)
    ink = (np.abs(strip - bg) > 40).sum(axis=1)
    rows = ink > 2
    pts, y, n = [], 0, len(rows)
    while y < n:
        if rows[y]:
            y2 = y
            while y2 < n and rows[y2]:
                y2 += 1
            if y2 - y > 8:
                pts.append((int(W * RAIL_X), int(H * 0.075) + (y + y2) // 2))
            y = y2
        y += 1
    return pts[:max_icons]


def rail_selected(s: Shot, pt: tuple[int, int]) -> bool:
    """The selected rail icon sits on a solid blue square."""
    x, y = pt
    blue = 0
    for dx, dy in ((-30, -30), (30, -30), (-30, 30), (30, 30), (-34, 0), (34, 0)):
        r, g, b = s.image.getpixel((max(0, x + dx), max(0, y + dy)))[:3]
        if b > 200 and r < 110 and g > 110:
            blue += 1
    return blue >= 3


def click_rail(index: int, s: Shot | None = None) -> Shot:
    s = s or shot("rail")
    pts = rail_points(s)
    if index >= len(pts):
        raise RuntimeError(f"rail has only {len(pts)} icons")
    for attempt in range(3):
        I.click(*pts[index], shot=s)
        s2, ok = wait_for(lambda sh: rail_selected(sh, pts[index]), timeout=3, poll=0.4,
                          tag=f"rail-{index}")
        if ok:
            time.sleep(0.4)
            return shot(f"rail-{index}")
        s = s2
    raise RuntimeError(f"rail icon {index} did not get selected; see {s.path}")


def list_categories(tab: str | None = None) -> list[dict]:
    """Click every rail icon of the current (or given) tab and read the panel
    title. This is how the harness learns what actually exists in the UI."""
    if tab:
        open_tab(tab)
    s = shot("rail")
    out = []
    pts = rail_points(s)
    if not any(rail_selected(s, p) for p in pts):
        return []   # this tab has no icon rail (e.g. Accessories)
    for i, (x, y) in enumerate(pts):
        s = click_rail(i, s)
        out.append({"index": i, "point": [x, y], "title": panel_title(s)})
    return out


def open_category(name: str, tab: str | None = None) -> Shot:
    """Select a left-rail sub-category by its panel title (e.g. 'ベースヘアー', '目')."""
    if tab:
        open_tab(tab)
    s = shot("cat")
    want = L.norm(name)
    if want in L.norm(panel_title(s)):
        return s
    for i, _ in enumerate(rail_points(s)):
        s = click_rail(i, s)
        t = L.norm(panel_title(s))
        if want in t or (t and t in want):
            return s
    raise RuntimeError(f"no rail category titled {name!r} on this tab")


# --- parameter panel ------------------------------------------------------------

def num(t: str) -> float:
    return float(t.replace(" ", "").replace(",", "."))


def _right_lines(s: Shot) -> list[L.Match]:
    return L.ocr(s, region=box(s, PANEL_RIGHT))


def read_panel(s: Shot | None = None) -> list[dict]:
    """Label/value pairs visible in the right panel right now."""
    s = s or shot("panel")
    lines = _right_lines(s)
    nums = [m for m in lines if NUM_RE.match(m.text.replace(" ", ""))]
    hexes = [m for m in lines if HEX_RE.fullmatch(m.text.strip().lstrip("#")) or m.text.strip().startswith("#")]
    labels = [m for m in lines if m not in nums and m not in hexes]
    out = []
    for lab in labels:
        row = {"label": lab.text, "y": lab.center[1]}
        v = [n for n in nums if abs(n.center[1] - lab.center[1]) < max(14, lab.height) and n.left > lab.left]
        if v:
            row["value"] = num(v[0].text)
            row["value_box"] = v[0].center
        hx = [h for h in hexes if 0 <= h.center[1] - lab.center[1] < lab.height * 4]
        if hx:
            row["hex"] = hx[0].text
        out.append(row)
    return out


def scroll_panel(ticks: int, s: Shot | None = None, side: str = "right") -> None:
    s = s or shot()
    b = box(s, PANEL_RIGHT if side == "right" else PANEL_LEFT)
    I.scroll(ticks, (b[0] + b[2]) // 2, int(s.size[1] * 0.6), shot=s)


def list_parameters(max_pages: int = 15) -> list[dict]:
    """Scroll the whole right panel top->bottom, collecting every row."""
    s = shot()
    scroll_panel(-60, s)
    time.sleep(0.4)
    seen, out, stale = set(), [], 0
    for _ in range(max_pages):
        s = shot("params")
        new = 0
        for r in read_panel(s):
            if "value" not in r and "hex" not in r:
                continue
            if r["label"] not in seen:
                seen.add(r["label"])
                out.append({k: v for k, v in r.items() if k not in ("y", "value_box")})
                new += 1
        stale = stale + 1 if new == 0 else 0
        if stale >= 2:
            break
        scroll_panel(6, s)
        time.sleep(0.9)  # let the smooth scroll settle, or OCR reads motion blur
    return out


def find_parameter(label: str, max_pages: int = 15) -> tuple[Shot, L.Match]:
    """Scroll the right panel from the top until a row labelled `label`
    (exact first, then substring) is fully visible."""
    s = shot()
    scroll_panel(-60, s)
    time.sleep(0.4)
    prev = None
    for _ in range(max_pages):
        s = shot(f"find-{label}")
        lines = _right_lines(s)
        m = (L.find_text(s, label, exact=True, lines=lines)
             or L.find_text(s, label, lines=lines))
        if m and m.center[1] < s.size[1] * 0.9:
            return s, m
        sig = " ".join(x.text for x in lines)
        if sig == prev:
            break  # bottom reached
        prev = sig
        scroll_panel(5, s)
        time.sleep(0.9)
    raise RuntimeError(f"parameter {label!r} not found in the right panel")


def _value_box(s: Shot, row: L.Match) -> tuple[int, int]:
    lines = _right_lines(s)
    cands = [m for m in lines if NUM_RE.match(m.text.replace(" ", ""))
             and abs(m.center[1] - row.center[1]) < max(16, row.height) and m.left > row.left]
    if cands:
        return cands[0].center
    return int(s.size[0] * 0.97), row.center[1]


def read_value(s: Shot, row: L.Match) -> float | None:
    W = s.size[0]
    region = (int(W * 0.9), row.top - 10, int(W * 0.995), row.top + row.height + 10)
    for m in L.ocr(s, region=region):
        t = m.text.replace(" ", "")
        if NUM_RE.match(t):
            return num(t)
    return None


def _type_into_box(s: Shot, x: int, y: int, text: str) -> None:
    """Unity input field: one click focuses and selects all. End + backspaces
    make the clear independent of that; Cmd+A is *not* used (Unity routes it
    unpredictably)."""
    I.click(x, y, shot=s)
    time.sleep(0.3)
    I.ascii_input_source()   # a kana IME turns keycodes into ひらがな
    I.key("end")
    I.key("backspace", times=14)
    I.type_text(text, mode="keys")
    time.sleep(0.15)
    I.key("return")
    time.sleep(0.8)


def set_parameter(label: str, value: float, tol: float = 0.0015) -> dict:
    """Type an exact value into a slider row's numeric box and read it back."""
    s, row = find_parameter(label)
    before = read_value(s, row)
    x, y = _value_box(s, row)
    _type_into_box(s, x, y, f"{value:.3f}")
    s2 = shot(f"set-{label}")
    row2 = (L.find_text(s2, label, exact=True, region=box(s2, PANEL_RIGHT))
            or L.find_text(s2, label, region=box(s2, PANEL_RIGHT)))
    if row2 is None:          # panel reflowed (e.g. 'save as custom item' appeared)
        s2, row2 = find_parameter(label)
    after = read_value(s2, row2)
    ok = after is not None and abs(after - value) <= max(tol, abs(value) * 0.002)
    S.log("set_parameter", label=label, value=value, before=before, after=after, ok=ok,
          shot=str(s2.path))
    return {"label": label, "requested": value, "before": before, "after": after,
            "ok": ok, "clamped": (after is not None and not ok), "shot": str(s2.path)}


# --- menu / save / native panels -------------------------------------------------

MENU = {
    "save": ["保存", "Save"],
    "save_as": ["名前を付けて保存", "Save As"],
    "import_item": ["カスタムアイテムをインポート", "Import Custom Item"],
    "undo": ["元に戻す", "Undo"],
    "redo": ["やり直し", "Redo"],
    "back_to_models": ["モデル選択に戻る", "Back to model selection"],
}


def open_menu() -> Shot:
    s = shot("pre-menu")
    I.click(*frac(s, *HAMBURGER), shot=s)
    s2, m = wait_for(lambda sh: L.find_text(sh, MENU["save_as"], region=(0, 0, int(sh.size[0] * 0.3),
                                                                         int(sh.size[1] * 0.4))),
                     timeout=5, tag="menu")
    if not m:
        raise RuntimeError(f"hamburger menu did not open; see {s2.path}")
    return s2


def menu_item(key: str) -> Shot:
    if key == "back_to_models" and is_dirty():
        raise RuntimeError("unsaved changes ('*' in title) - save before leaving the model")
    s = open_menu()
    names = MENU[key]
    region = (0, 0, int(s.size[0] * 0.3), int(s.size[1] * 0.4))
    m = L.find_text(s, names, exact=True, region=region) or L.find_text(s, names, region=region)
    if m is None:
        I.key("escape")
        raise RuntimeError(f"menu has no {names}")
    I.click(*m.center, shot=s)
    time.sleep(1.2)
    return shot(f"menu-{key}")


def project_title() -> str:
    w = M.main_window()
    return w.title if w else ""


def is_dirty() -> bool:
    return project_title().rstrip().endswith("*")


def _panel_present(s: Shot) -> L.Match | None:
    """The native save panel shows a '名前:'/'Name:' row and a 保存/Save button."""
    return L.find_text(s, ["名前:", "Name:", "名前", "Save As:"], region=box(s, (0.3, 0.3, 0.7, 0.7)))


def safe_name(name: str) -> str:
    """File names typed into native panels must be plain keycodes: [A-Za-z0-9.-]."""
    stem = re.sub(r"[^A-Za-z0-9.-]+", "-", Path(name).stem).strip("-.") or "character"
    return stem[:60]


def _panel_field(text: str) -> None:
    """Replace the focused native text field's contents.

    Measured on macOS 26 + VRoid 2.14: the panel is an out-of-process remote
    view. It ignores unicode-payload key events and Cmd+A (VRoid's menu eats
    the key equivalent), but honours physical keycodes and the Cocoa text
    bindings Ctrl+A (line start) / Ctrl+K (kill to end)."""
    I.key("ctrl+a", allow_panel=True)
    I.key("ctrl+k", allow_panel=True)
    I.type_text(text, mode="keys", allow_panel=True)


def drive_save_panel(target: Path, timeout: float = 120.0) -> Path:
    """Fill the native save panel VRoid opened: Go to Folder (Cmd+Shift+G) ->
    folder path -> Return -> file name -> Return. Refuses to overwrite."""
    s, m = wait_for(_panel_present, timeout=15, tag="save-panel")
    if not m:
        raise RuntimeError(f"save panel did not appear; see {s.path}")
    if target.exists():
        I.key("escape", allow_panel=True)
        raise FileExistsError(f"{target} exists - refusing to overwrite")
    I.ascii_input_source()                     # IME off: keycodes must not become kana
    I.key("cmd+shift+g", allow_panel=True)
    time.sleep(1.2)
    _panel_field(str(target.parent))
    time.sleep(0.8)
    s = shot("save-goto")
    I.key("return", allow_panel=True)
    time.sleep(1.2)
    s = shot("save-panel-folder")
    if not L.find_text(s, target.parent.name, region=box(s, (0.3, 0.3, 0.7, 0.7))):
        I.key("escape", allow_panel=True)
        raise RuntimeError(f"panel did not switch to {target.parent}; see {s.path}")
    _panel_field(target.name)
    time.sleep(0.5)
    s = shot("save-panel-filled")
    if not L.find_text(s, target.stem[:16], region=box(s, (0.3, 0.3, 0.7, 0.7))):
        I.key("escape", allow_panel=True)
        raise RuntimeError(f"file name did not take in the panel; see {s.path}")
    I.key("return", allow_panel=True)
    time.sleep(1.5)
    s = shot("save-panel-after")
    if L.find_text(s, ["置き換え", "Replace", "既に存在", "already exists"]):
        I.key("escape", allow_panel=True)
        raise FileExistsError("panel asked to replace an existing file - cancelled")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if target.exists() and target.stat().st_size > 1024:
            size = target.stat().st_size
            time.sleep(1.5)
            if target.stat().st_size == size:
                return target
        time.sleep(1.0)
    raise TimeoutError(f"{target} was not written; see {s.path}")


def save_project_as(name: str) -> dict:
    target = S.unique_path(S.PROJECTS, safe_name(name), ".vroid")
    menu_item("save_as")
    p = drive_save_panel(target)
    s = shot("saved")
    r = {"path": str(p), "bytes": p.stat().st_size, "title": project_title(), "shot": str(s.path)}
    S.log("save_project", **r)
    return r


def save_project() -> dict:
    """Menu > 保存 for a project that already has a file, verified by the '*'
    leaving the window title (it lags the write by a few seconds - poll, do
    not judge after 2 s). Untitled projects open a save panel - then this
    refuses; use save_project_as."""
    if not is_dirty():
        return {"title": project_title(), "dirty": False, "note": "nothing to save"}
    if "名称未設定" in project_title() or "Untitled" in project_title():
        raise RuntimeError("project is untitled - use save_project_as(name)")
    menu_item("save")
    deadline = time.time() + 60
    while time.time() < deadline and is_dirty():
        s = shot("saving")
        if _panel_present(s):
            I.key("escape", allow_panel=True)
            raise RuntimeError("save opened a panel - use save_project_as(name)")
        time.sleep(1.0)
    r = {"title": project_title(), "dirty": is_dirty()}
    if r["dirty"]:
        raise RuntimeError(f"save did not complete: {r}")
    S.log("save_project", **r)
    return r


# --- VRM export -------------------------------------------------------------------

EXPORT_MENU = ["VRMエクスポート", "Export as VRM", "VRM Export"]
EXPORT_BTN = ["エクスポート", "Export"]
MODAL_TITLE = ["VRMエクスポート設定", "VRM Export Settings", "VRM Settings"]
MODAL = (0.33, 0.1, 0.67, 1.0)


def _modal_open(s: Shot):
    return L.find_text(s, MODAL_TITLE, region=box(s, (0.3, 0.15, 0.7, 0.3)))


def _set_modal_field(caption: list[str], text: str) -> None:
    """Captions sit above their text box; scroll the modal to the top first."""
    s = shot("modal-field")
    lab = L.find_text(s, caption, region=box(s, MODAL))
    if lab is None:
        raise RuntimeError(f"VRM settings has no field {caption}; see {s.path}")
    I.click(s.size[0] // 2, lab.center[1] + int(0.034 * s.size[1]), shot=s)
    time.sleep(0.3)
    I.key("end")
    I.key("backspace", times=max(40, len(text) + 10))
    I.type_text(text)
    time.sleep(0.4)
    s = shot("modal-field-set")
    if not L.find_text(s, text[:10], region=box(s, MODAL)):
        raise RuntimeError(f"field {caption[0]} did not take {text!r}; see {s.path}")


def export_vrm(name: str, avatar_name: str, author: str, version: str = "",
               fmt: str = "1.0") -> dict:
    """Editor -> share icon -> VRMエクスポート -> エクスポート -> settings modal
    (format, avatar name, author, version) -> エクスポート -> native save panel.
    Licence options are left at VRoid's defaults (prohibitive). Returns the
    verified file path and size."""
    target = S.unique_path(S.EXPORTS, safe_name(name), ".vrm")
    s = shot("export-start")
    scr = current_screen(s)["screen"]
    if scr == "editor":
        I.click(*frac(s, *TB_EXPORT), shot=s)
        s, m = wait_for(lambda sh: L.find_text(sh, EXPORT_MENU, region=box(sh, (0.6, 0.03, 1, 0.2))),
                        timeout=6, tag="export-menu")
        if not m:
            raise RuntimeError(f"share menu has no VRM export entry; see {s.path}")
        I.click(*m.center, shot=s)
        s, _ = wait_for(lambda sh: current_screen(sh)["screen"] == "export", timeout=30, tag="export-screen")
    if not _modal_open(s):
        m = L.find_text(s, EXPORT_BTN, exact=True, region=box(s, (0.75, 0.12, 1, 0.3)))
        if m is None:
            raise RuntimeError(f"no エクスポート button on the export screen; see {s.path}")
        I.click(*m.center, shot=s)
        s, ok = wait_for(_modal_open, timeout=20, tag="vrm-settings")
        if not ok:
            raise RuntimeError(f"VRM export settings did not open; see {s.path}")
    W, H = s.size
    I.scroll(-40, W // 2, int(H * 0.6), shot=s)
    time.sleep(0.8)
    s = shot("modal-top")
    want = "VRM0.0" if str(fmt).startswith("0") else "VRM1.0"
    r = L.find_text(s, [want], region=box(s, MODAL))
    if r:
        I.click(r.left - int(0.012 * W), r.center[1], shot=s)
        time.sleep(0.4)
    _set_modal_field(["アバター名", "Avatar Name"], avatar_name)
    if version:
        _set_modal_field(["バージョン", "Version"], version)
    _set_modal_field(["作者", "Creators", "Author"], author)
    s = shot("modal")
    for _ in range(8):
        I.scroll(10, W // 2, int(H * 0.6), shot=s)
        time.sleep(0.6)
        s = shot("modal-scroll")
        b = L.find_text(s, EXPORT_BTN, exact=True, region=box(s, (0.4, 0.5, 0.6, 1)))
        if b:
            break
    else:
        raise RuntimeError(f"no エクスポート button inside the modal; see {s.path}")
    I.click(*b.center, shot=s)
    p = drive_save_panel(target, timeout=240)
    head = p.read_bytes()[:4]
    s = shot("exported")
    res = {"path": str(p), "bytes": p.stat().st_size, "glb_magic_ok": head == b"glTF",
           "format": want, "shot": str(s.path)}
    S.log("export_vrm", **res)
    return res


def leave_export_screen() -> Shot:
    return close_subeditor()


def _old_leave_export_screen() -> Shot:
    s = shot()
    if current_screen(s)["screen"] == "export":
        I.click(int(0.013 * s.size[0]), int(0.048 * s.size[1]), shot=s)   # ✕ top-left
        s, _ = wait_for(lambda sh: current_screen(sh)["screen"] == "editor", timeout=15, tag="back-editor")
    return s


CLOSE_X = (0.014, 0.048)   # ✕ of full-screen sub-editors (texture / export / hair)


def close_subeditor(tries: int = 3) -> Shot:
    """Leave texture editor / export screen via ✕ until the editor is back.
    A pending colour popup swallows the first click, hence the retry."""
    s = shot("close-sub")
    for _ in range(tries):
        if current_screen(s)["screen"] == "editor":
            return s
        I.click(*frac(s, *CLOSE_X), shot=s)
        s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "editor", timeout=6, tag="closed-sub")
        if ok:
            return s
    raise RuntimeError(f"could not return to the editor; see {s.path}")


# --- colours ----------------------------------------------------------------------

def hex_norm(h: str) -> str:
    h = h.strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", h):
        raise ValueError(f"not a #RRGGBB colour: {h!r}")
    return h


def shade_of(h: str, f: float = 0.72) -> str:
    """Darker companion colour for かげ色 (shadow)."""
    h = hex_norm(h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "".join(f"{max(0, min(255, int(c * f))):02X}" for c in (r, g, b))


def _color_close(px: str, h: str, tol: int = 18) -> bool:
    a = [int(px.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


TITLE_SAFE = (0.5, 0.048)   # title text of full-screen editors: a click there does nothing


def close_popup() -> None:
    """Dismiss a colour popup. Escape works only sometimes, and clicking the
    texture editor's canvas or 3D view would PAINT - so click the title text."""
    I.key("escape")
    time.sleep(0.4)
    s = shot("popup-check")
    if any(ocr_hex(m.text) for m in L.ocr(s, region=box(s, (0.05, 0.07, 0.6, 1.0)))):
        I.click(*frac(s, *TITLE_SAFE), shot=s)
        time.sleep(0.5)


_HEX_FIX = str.maketrans({"I": "1", "l": "1", "|": "1", "i": "1", "O": "0", "o": "0",
                          "Q": "0", "S": "5", "s": "5", "G": "6", "Z": "2", "z": "2", "g": "9"})


def ocr_hex(t: str) -> str | None:
    """'#BICDD3' -> 'B1CDD3' (Vision confuses I/1, O/0 in hex boxes, and
    with ja enabled sometimes reads '#' as full-width '＃')."""
    t = unicodedata.normalize("NFKC", t).strip().replace(" ", "")
    if not t.startswith("#"):
        return None
    h = t[1:].translate(_HEX_FIX).upper()
    return h if re.fullmatch(r"[0-9A-F]{6}", h) else None


def _hex_boxes(s: Shot) -> list[L.Match]:
    return [m for m in L.ocr(s) if ocr_hex(m.text)]


def open_swatch_popup(sw: tuple[int, int]) -> tuple[Shot, L.Match]:
    """Click a colour swatch and return the popup's hex box.

    The popup is confirmed by pixels changing next to the swatch, and its
    hex box is read by OCR of *that region only* (full-window OCR misses the
    small text). We never type unless that box was found - stray keys land on
    the canvas, where letters are tool shortcuts."""
    s = shot("swatch-before")
    W, H = s.size
    reg = (max(0, sw[0] - int(0.02 * W)), max(0, sw[1] - int(0.12 * H)),
           min(W, sw[0] + int(0.25 * W)), min(H, sw[1] + int(0.45 * H)))
    I.click(*sw, shot=s)
    time.sleep(0.9)
    s2 = shot("popup")
    if L.ui_diff(s, s2, reg) < 0.05:
        raise RuntimeError(f"colour popup did not open for swatch {sw}; see {s2.path}")
    boxes = [m for m in L.ocr(s2, region=reg) if ocr_hex(m.text)]
    for _ in range(3):  # the popup fades in; its hex text can lag the frame
        if boxes:
            break
        time.sleep(0.6)
        s2 = shot("popup")
        boxes = [m for m in L.ocr(s2, region=reg) if ocr_hex(m.text)]
    if not boxes:
        close_popup()
        raise RuntimeError(f"popup opened but its hex box was not read; see {s2.path}")
    return s2, min(boxes, key=lambda m: abs(m.center[0] - sw[0]) + abs(m.center[1] - sw[1]))


def _type_hex_in_popup(s: Shot, hb: L.Match, hexcode: str) -> bool:
    _type_into_box(s, *hb.center, hexcode)
    s2 = shot("hex-set")
    reg = (hb.left - 80, hb.top - 40, hb.left + hb.width + 80, hb.top + hb.height + 40)
    return any(ocr_hex(m.text) == hexcode for m in L.ocr(s2, region=reg))


def set_swatch(sw: tuple[int, int], hexcode: str) -> bool:
    s, hb = open_swatch_popup(sw)
    ok = _type_hex_in_popup(s, hb, hexcode)
    close_popup()
    return ok


def set_hex_row(label: str | list[str], hexcode: str) -> dict:
    """Right-panel colour row (e.g. メインカラー / ハイライトカラー): the
    '#RRGGBB' box sits on the line below the caption."""
    hexcode = hex_norm(hexcode)
    s = shot("hexrow")
    reg = box(s, PANEL_RIGHT)
    lines = L.ocr(s, region=reg)
    lab = L.find_text(s, label, lines=lines)
    if lab is None:
        s, lab = find_parameter(label if isinstance(label, str) else label[0])
        lines = L.ocr(s, region=reg)
    below = [m for m in lines if ocr_hex(m.text) and 0 < m.center[1] - lab.center[1] < lab.height * 5]
    if not below:
        raise RuntimeError(f"no #hex box under {label}; see {s.path}")
    hb = min(below, key=lambda m: m.center[1])
    _type_into_box(s, *hb.center, hexcode)
    s2 = shot("hexrow-set")
    ok = any(ocr_hex(m.text) == hexcode for m in L.ocr(s2, region=reg))
    return {"label": lab.text, "hex": "#" + hexcode, "ok": ok, "shot": str(s2.path)}


# --- texture editor (outfit / iris colours) -------------------------------------

TEX_LEFT = (0.0, 0.07, 0.17, 1.0)
CLOSE_DIALOG = ["テクスチャ編集を終了する", "Finish editing texture"]
DISCARD_ITEM = ["保存せずに編集を終了", "Finish without saving"]


def open_texture_editor() -> Shot:
    s = shot("pre-tex")
    m = L.find_text(s, ["テクスチャを編集", "Edit Texture"], region=box(s, PANEL_RIGHT))
    if m is None:
        raise RuntimeError("this category has no テクスチャを編集 button")
    I.click(*m.center, shot=s)
    s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "texture_editor", timeout=20, tag="tex")
    if not ok:
        raise RuntimeError(f"texture editor did not open; see {s.path}")
    time.sleep(1.0)
    return shot("tex-ready")


def _shader_rows(s: Shot) -> list[dict]:
    """Visible 基本色/かげ色 rows with their swatch colour, plus the nearest
    section header above each (material name, truncated by the UI)."""
    reg = box(s, TEX_LEFT)
    lines = L.ocr(s, region=reg)
    heads = [m for m in lines if m.left < s.size[0] * 0.07 and not any(
        k in m.text for k in ("テクスチャ", "レイヤー", "シェーダー", "基本色", "かげ色", "衣装", "肌マスク"))]
    rows = []
    for m in lines:
        kind = "base" if "基本色" in m.text else "shade" if "かげ色" in m.text else None
        if not kind:
            continue
        cap = L._narrow(m, L.norm("基本色" if kind == "base" else "かげ色"))
        sw = (cap.left - int(0.0095 * s.size[0]), m.center[1])   # swatch ~33px left of caption
        above = [h for h in heads if h.center[1] < m.center[1]]
        rows.append({"kind": kind, "label": m, "swatch": sw, "color": L.pixel(s, *sw),
                     "section": above[-1].text if above else ""})
    return rows


_SUB = ("テクスチャ", "レイヤー", "シェーダー", "基本色", "かげ色", "衣装", "肌マスク", "Texture", "Layer", "Shader")


def _tex_scroll_x(s: Shot) -> int:
    # wheel over the panel's scrollbar strip: the cards contain nested layer
    # lists that swallow wheel events when the pointer is over them
    return box(s, TEX_LEFT)[2] - int(0.0035 * s.size[0])


def _tex_headers(s: Shot, lines: list[L.Match] | None = None) -> list[L.Match]:
    """Material section headers, found by their chevron glyph in the column
    x = 0.039..0.048 W (sub-blocks テクスチャ/シェーダーカラー have theirs at
    ~0.061 W), then named by the OCR text on the same row."""
    import numpy as np
    W, H = s.size
    reg = box(s, TEX_LEFT)
    lines = lines if lines is not None else L.ocr(s, region=reg)
    col = np.asarray(s.image.crop((int(W * 0.039), int(H * 0.11), int(W * 0.048), H)).convert("L"))
    dark = (col < 200).sum(axis=1) > 0     # chevrons are ~160-175 grey; backgrounds 236-255
    heads, y, n = [], 0, len(dark)
    while y < n:
        if dark[y]:
            y2 = y
            while y2 < n and dark[y2]:
                y2 += 1
            if 8 <= y2 - y <= 40:
                cy = int(H * 0.11) + (y + y2) // 2
                row = [m for m in lines if abs(m.center[1] - cy) < 22 and m.left < W * 0.12
                       and len(L.norm(m.text)) >= 2]
                txt = " ".join(m.text for m in sorted(row, key=lambda m: m.left)) or "?"
                heads.append(L.Match(txt, 1.0, int(W * 0.0435), cy - 15, int(W * 0.1), 30))
            y = y2
        y += 1
    return heads


def _tex_expanded(s: Shot, h: L.Match, lines: list[L.Match]) -> bool:
    nxt = [m for m in lines if m.center[1] > h.center[1] + 10]
    nxt.sort(key=lambda m: m.center[1])
    return bool(nxt) and any(k in nxt[0].text for k in ("テクスチャ", "Texture", "シェーダー", "Shader"))


def _toggle(s: Shot, h: L.Match) -> None:
    I.click(int(0.0435 * s.size[0]), h.center[1], shot=s)   # chevron left of the header
    time.sleep(0.7)


def tex_collapse_all(max_rounds: int = 12) -> Shot:
    """Collapse every material section so all headers fit on one screen."""
    for _ in range(max_rounds):
        s = shot("tex-collapse")
        I.scroll(-80, _tex_scroll_x(s), int(s.size[1] * 0.6), shot=s)
        time.sleep(0.8)
        s = shot("tex-collapse")
        lines = L.ocr(s, region=box(s, TEX_LEFT))
        open_heads = [h for h in _tex_headers(s, lines) if _tex_expanded(s, h, lines)]
        if not open_heads:
            return s
        _toggle(s, open_heads[0])
    raise RuntimeError("could not collapse the texture editor sections")


def _section_rows(s: Shot, h: L.Match) -> list[dict]:
    heads = sorted(_tex_headers(s), key=lambda m: m.center[1])
    nxt = [x.center[1] for x in heads if x.center[1] > h.center[1] + 10]
    lim = nxt[0] if nxt else s.size[1]
    return [r for r in _shader_rows(s) if h.center[1] < r["label"].center[1] < lim]


def tex_sections(action=None) -> list[dict]:
    """For each material section: expand it, read (and optionally act on)
    its 基本色/かげ色 rows, collapse it again. Deterministic - no scroll
    tracking. Returns [{index, section, base, shade, ...}]."""
    s = tex_collapse_all()
    heads = sorted(_tex_headers(s), key=lambda m: m.center[1])
    names = [h.text for h in heads]
    out = []
    for i, name in enumerate(names):
        s = shot("tex-sec")
        h = sorted(_tex_headers(s), key=lambda m: m.center[1])[i]
        _toggle(s, h)
        s = shot(f"tex-sec-{i}")
        h = sorted(_tex_headers(s), key=lambda m: m.center[1])[i]
        rows = _section_rows(s, h)
        if rows and max(r["label"].center[1] for r in rows) > s.size[1] * 0.88:
            # section taller than the remaining space: scroll so the header is near the top
            I.scroll(int((h.center[1] - s.size[1] * 0.12) / (100 * s.size[1] / 2168)),
                     _tex_scroll_x(s), int(s.size[1] * 0.6), shot=s)
            time.sleep(0.8)
            s = shot(f"tex-sec-{i}")
            hs = [x for x in _tex_headers(s) if L.norm(x.text) == L.norm(h.text)]
            h = hs[0] if hs else h
            rows = _section_rows(s, h)
        if not rows:            # e.g. the 'template limit reached' note under the list
            s = tex_collapse_all()
            continue
        rec = {"index": len(out), "section": name.lstrip("♥<>＞〈 ").strip()}
        for r in rows:
            rec[r["kind"]] = r["color"]
        if action:
            rec.update(action(rec, rows, s) or {})
        out.append(rec)
        s = tex_collapse_all()
    return out


def list_shader_colors() -> list[dict]:
    """Material sections of the open texture editor with their current
    基本色 / かげ色 swatch colours (index = top-to-bottom section order)."""
    return tex_sections()


def set_shader_colors(base: str, shade: str | None = None, sections: list[int] | None = None,
                      match: str | None = None) -> dict:
    """Set 基本色 (and かげ色, default = base darkened to 72%) on material
    sections of the open texture editor. sections: indices from
    list_shader_colors (None = all); match: header substring filter. Already-
    correct swatches are skipped, so re-running is safe."""
    base = hex_norm(base)
    shade = hex_norm(shade) if shade else shade_of(base)

    def act(rec, rows, s):
        if sections is not None and rec["index"] not in sections:
            return None
        if match and match not in rec["section"]:
            return None
        res = {}
        for r in rows:
            want = base if r["kind"] == "base" else shade
            if _color_close(r["color"], want):
                res[r["kind"] + "_ok"] = True
                continue
            s = shot("tex-swatch")
            fresh = [x for x in _shader_rows(s) if x["kind"] == r["kind"]
                     and abs(x["label"].center[1] - r["label"].center[1]) < 30]
            sw = fresh[0]["swatch"] if fresh else r["swatch"]
            res[r["kind"] + "_ok"] = set_swatch(sw, want)
            res[r["kind"]] = "#" + want
        return res

    secs = tex_sections(act)
    touched = [x for x in secs if "base_ok" in x or "shade_ok" in x]
    return {"sections": secs, "all_ok": bool(touched) and all(
        x.get("base_ok", True) and x.get("shade_ok", True) for x in touched)}


def close_texture_editor(save_item: bool = False) -> Shot:
    """✕ -> 'テクスチャ編集を終了する' dialog -> 保存せずに編集を終了 (default).
    The edit stays on the model; it is just not stored as a closet item."""
    for _ in range(3):
        s = shot("tex-close")
        if current_screen(s)["screen"] == "editor":
            return s
        d = L.find_text(s, CLOSE_DIALOG)
        if d is None:
            I.key("escape")       # dismiss a colour popup if one is open
            time.sleep(0.4)
            I.click(*frac(s, *CLOSE_X), shot=s)
            s, d = wait_for(lambda sh: L.find_text(sh, CLOSE_DIALOG) or current_screen(sh)["screen"] == "editor",
                            timeout=6, tag="tex-close-dialog")
            if d is True:
                return s
        if d:
            label = ["新規アイテムとして保存", "Save as new item"] if save_item else DISCARD_ITEM
            b = L.find_text(s, label)
            if b is None:
                raise RuntimeError(f"close dialog has no {label}; see {s.path}")
            I.click(*b.center, shot=s)
            s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "editor", timeout=10, tag="tex-closed")
            if ok:
                return s
    raise RuntimeError(f"texture editor did not close; see {s.path}")


def _swatch_left_of(s: Shot, cap: L.Match) -> tuple[int, int]:
    """Centre of the colour circle left of a caption: scan leftwards from the
    caption for the first non-background run on the caption's row."""
    y = cap.center[1]
    bg = s.image.getpixel((cap.left - 4, y))[:3]
    x, run = cap.left - 6, []
    while x > cap.left - int(0.03 * s.size[0]):
        px = s.image.getpixel((x, y))[:3]
        diff = max(abs(a - b) for a, b in zip(px, bg))
        if diff > 10:
            run.append(x)
        elif run and len(run) > 6:
            break
        x -= 1
    if not run:
        return cap.left - int(0.0095 * s.size[0]), y
    return (min(run) + max(run)) // 2, y


def set_caption_swatch(caption: list[str], hexcode: str, region=TEX_LEFT) -> bool:
    """Click the swatch left of `caption`, type the hex in the popup, close it."""
    hexcode = hex_norm(hexcode)
    s = shot("swatch")
    cap = L.find_text(s, caption, region=box(s, region))
    if cap is None:
        raise RuntimeError(f"no {caption} on screen; see {s.path}")
    sw = _swatch_left_of(s, cap)
    if _color_close(L.pixel(s, *sw), hexcode, tol=10):
        return True
    return set_swatch(sw, hexcode)


def set_checkbox(caption: list[str], on: bool = True, region=TEX_LEFT) -> bool:
    """Checkbox left of a caption; checked = blue fill."""
    s = shot("checkbox")
    cap = L.find_text(s, caption, region=box(s, region))
    if cap is None:
        raise RuntimeError(f"no checkbox {caption}; see {s.path}")
    bx = _swatch_left_of(s, cap)
    r, g, b = s.image.getpixel(bx)[:3]
    checked = b > 200 and r < 120
    if checked != on:
        I.click(*bx, shot=s)
        time.sleep(0.6)
        s = shot("checkbox-after")
        r, g, b = s.image.getpixel(bx)[:3]
        checked = b > 200 and r < 120
    return checked == on


def change_eye_color(hexcode: str, right: str | None = None) -> dict:
    """顔 > 瞳 > テクスチャを編集 > カラー調整: enable it, set left/right eye colour."""
    open_category("瞳", tab="face")
    open_texture_editor()
    en = set_checkbox(["カラー調整を使う", "Use color adjustment"], True)
    l_ok = set_caption_swatch(["カラー（左目）", "カラー(左目)", "左目"], hexcode)
    r_ok = set_caption_swatch(["カラー（右目）", "カラー(右目)", "右目"], right or hexcode)
    s = shot("eye-colored")
    close_texture_editor()
    return {"enabled": en, "left_ok": l_ok, "right_ok": r_ok, "shot": str(s.path)}


# --- preset grids -------------------------------------------------------------------

def preset_tiles(s: Shot) -> list[L.Match]:
    """Thumbnail tiles of the left preset grid: light grey/blue rounded rects."""
    import cv2
    import numpy as np
    W, H = s.size
    reg = box(s, (0.028, 0.08, 0.165, 0.95))
    a = np.asarray(s.image.crop(reg).convert("RGB")).astype(np.int16)
    mask = ((a.min(axis=2) >= 215) & (a.max(axis=2) <= 254)).astype(np.uint8)   # grey + pale-blue tiles, not white
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 4)
    out = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if w > 0.04 * W and h > 0.05 * H and area > 0.3 * w * h:
            out.append(L.Match("<tile>", 1.0, int(x + reg[0]), int(y + reg[1]), int(w), int(h)))
    out.sort(key=lambda m: (round(m.center[1] / (0.03 * H)), m.left))
    # The grid is regular: a thumbnail whose artwork touches its edges splits
    # its background mask, so infer a missing column from its row-mate.
    if len(out) >= 2:
        xs = sorted(m.center[0] for m in out)
        c0, c1 = xs[0], xs[-1]
        if c1 - c0 > 0.03 * W:
            extra = []
            for m in out:
                other = c1 if abs(m.center[0] - c0) < abs(m.center[0] - c1) else c0
                if not any(abs(o.center[0] - other) < 0.02 * W and abs(o.center[1] - m.center[1]) < 0.02 * H
                           for o in out + extra):
                    extra.append(L.Match("<tile*>", 0.5, int(other - m.width / 2), m.top, m.width, m.height))
            out = sorted(out + extra, key=lambda m: (round(m.center[1] / (0.03 * H)), m.left))
    return out


def _scroll_shift(a: Shot, b: Shot, reg: tuple, expect: int) -> int | None:
    """Pixels the panel content moved up from a to b, by template-matching
    strips of a inside b. Only shifts in [0, 1.6*expect] are accepted (rows
    look alike), the best-scoring strip wins. 0 = did not move."""
    import cv2
    import numpy as np
    A = cv2.cvtColor(np.asarray(a.image.crop(reg)), cv2.COLOR_RGB2GRAY)
    B = cv2.cvtColor(np.asarray(b.image.crop(reg)), cv2.COLOR_RGB2GRAY)
    h = A.shape[0]
    best = None
    for top in (int(h * 0.55), int(h * 0.7), int(h * 0.4)):
        strip = A[top:top + 160]
        if strip.std() < 8:
            continue
        res = cv2.matchTemplate(B, strip, cv2.TM_CCOEFF_NORMED)[:, 0]
        lo, hi = max(0, top - int(expect * 1.6)), min(len(res) - 1, top)
        if hi < lo:
            continue
        i = lo + int(np.argmax(res[lo:hi + 1]))
        if res[i] > 0.9 and (best is None or res[i] > best[0]):
            best = (float(res[i]), top - i)
    return None if best is None else best[1]


def _grid_x(s: Shot) -> int:
    return box(s, PANEL_LEFT)[2] - int(0.004 * s.size[0])   # over the grid's scrollbar


def wait_settled(reg_frac, timeout: float = 4.0, tag: str = "settle") -> Shot:
    """Re-shoot until a UI region stops changing (smooth scrolling animates
    for ~0.5-1.5 s; reading it mid-flight mislabels rows)."""
    prev = shot(tag)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        cur = shot(tag)
        if L.ui_diff(prev, cur, box(cur, reg_frac)) < 0.0005:
            return cur
        prev = cur
    return prev


GRID = (0.028, 0.08, 0.165, 0.95)


def _grid_scroll_to_top(s: Shot) -> Shot:
    """Unity drops part of a long wheel burst, so wheel up in batches until
    the grid stops moving."""
    for _ in range(12):
        I.scroll(-25, _grid_x(s), int(s.size[1] * 0.55), shot=s)
        time.sleep(0.4)
        s2 = wait_settled(GRID, tag="grid-top")
        if L.ui_diff(s, s2, box(s2, GRID)) < 0.0005:
            return s2
        s = s2
    return s


def _grid_pages(max_pages: int = 60):
    """Yield (shot, offset, tiles) from top to bottom; offset = content px
    scrolled so far (tile global y = tile.center.y + offset)."""
    s = _grid_scroll_to_top(shot("grid"))
    offset, stale = 0, 0
    reg = box(s, (0.028, 0.08, 0.165, 0.95))
    step = int(2 * 100 * s.size[1] / 2168)   # < one tile row, so every row is fully seen once
    for _ in range(max_pages):
        yield s, offset, preset_tiles(s)
        I.scroll(2, _grid_x(s), int(s.size[1] * 0.55), shot=s)
        time.sleep(0.4)
        s2 = wait_settled(GRID, tag="grid")
        dy = _scroll_shift(s, s2, reg, step)
        if not dy or dy <= 2:
            stale += 1
            if stale >= 2:
                return
            s = s2
            continue
        stale = 0
        offset += dy
        s = s2


def _iter_grid():
    """Yield (index, shot, tile) once per tile, top to bottom, the first time
    the tile is fully visible. Rows are numbered by order of appearance
    (global y = y + scrolled offset; a new row starts > 0.4 pitch below the
    previous one), so small errors in the scroll estimate cannot skip rows."""
    row_ys: list[float] = []
    seen: set[int] = set()
    for s, off, tiles in _grid_pages():
        H = s.size[1]
        hmax = max(t.height for t in tiles)
        full = [t for t in tiles if t.top >= H * 0.085 and t.top + t.height <= H * 0.94
                and t.height >= 0.92 * hmax]          # clipped by the panel edge otherwise
        if not full:
            continue
        xs = sorted(t.center[0] for t in tiles)
        mid = (xs[0] + xs[-1]) / 2
        pitch = full[0].height * 1.1
        for t in sorted(full, key=lambda t: (t.center[1], t.left)):
            gy = t.center[1] + off
            r = next((i for i, y in enumerate(row_ys) if abs(y - gy) < pitch * 0.4), None)
            if r is None:
                if row_ys and gy < row_ys[-1]:
                    continue           # above the last known row: already handled
                row_ys.append(gy)
                r = len(row_ys) - 1
            idx = r * 2 + (0 if t.center[0] < mid else 1)
            if idx in seen:
                continue
            if t.text == "<tile*>":               # inferred tile: skip if it is empty space
                import numpy as np
                if np.asarray(s.image.crop((t.left, t.top, t.left + t.width, t.top + t.height)).convert("L")).std() < 6:
                    continue
            seen.add(idx)
            yield idx, s, t


def scan_presets() -> tuple[list[dict], "Path"]:
    """Number every tile of the current preset grid and build a contact sheet
    (index drawn on each thumbnail) for Claude to choose from by eye."""
    from PIL import Image, ImageDraw, ImageFont
    items = []
    for idx, s, t in _iter_grid():
        items.append({"index": idx, "thumb": s.image.crop((t.left, t.top, t.left + t.width, t.top + t.height))})
    if not items:
        raise RuntimeError("no preset tiles found - is a preset grid (プリセット) showing?")
    items.sort(key=lambda i: i["index"])
    tw, th = items[0]["thumb"].size
    sc = 160 / tw
    cols = 6
    cell_h = int(th * sc + 10)
    sheet = Image.new("RGB", (cols * 170, ((len(items) + cols - 1) // cols) * cell_h), "white")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except Exception:
        font = None
    for i, it in enumerate(items):
        im = it.pop("thumb").resize((160, int(th * sc)))
        x, y = (i % cols) * 170, (i // cols) * cell_h
        sheet.paste(im, (x, y))
        d.rectangle((x, y, x + 44, y + 34), fill="black")
        d.text((x + 4, y + 2), str(it["index"]), fill="white", font=font)
    p = S.capture_path("preset-sheet")
    sheet.save(p)
    S.log("scan_presets", n=len(items), sheet=str(p))
    return items, p


def select_preset(index: int) -> dict:
    """Click tile `index` (numbering of scan_presets) and confirm the model
    changed in the viewport."""
    for idx, s, t in _iter_grid():
        if idx != index:
            continue
        I.click(*t.center, shot=s)
        time.sleep(2.5)
        after = shot(f"preset-{index}")
        changed = L.ui_diff(s, after, box(after, VIEWPORT))
        S.log("select_preset", index=index, viewport_change=changed)
        return {"index": index, "viewport_change": round(changed, 4),
                "changed": changed > 0.0005, "shot": str(after.path)}
    raise RuntimeError(f"preset tile {index} not found")


# --- camera ---------------------------------------------------------------------------
# Measured (2.14.0, macOS): right-drag in the viewport orbits the camera,
# 2.5 points of horizontal drag per degree (900 image px = 180 deg at 2x).

PT_PER_DEG = 2.5


def _vp_center(s: Shot) -> tuple[int, int]:
    b = box(s, VIEWPORT)
    return (b[0] + b[2]) // 2, int(s.size[1] * 0.45)


def _orbit_drag(s: Shot, dpx: float) -> None:
    cx, cy = _vp_center(s)
    # Measured: Unity loses rotation when a motion event moves < ~5 pt, so
    # every event moves >= 8 pt (fewer, bigger steps), and drags are kept
    # large (see rotate_camera for small angles).
    steps = int(max(1, min(24, abs(dpx) / s.scale / 8)))
    I.drag(round(cx - dpx / 2), cy, round(cx + dpx / 2), cy, shot=s, button="right", steps=steps)
    time.sleep(0.35)


def rotate_camera(deg: float, s: Shot | None = None) -> Shot:
    """Orbit around the model by `deg` (positive = drag right). Exact for
    any angle: 2.5 pt/deg, verified by 4 x 90 deg returning pixel-identical.
    Small angles are done as two large drags (+(deg+40), -40)."""
    s = s or shot("cam")
    if abs(deg) < 1:
        return s
    if abs(deg) < 30:
        sign = 1 if deg > 0 else -1
        parts = [deg + sign * 40, -sign * 40]
    else:
        parts = []
        rest = deg
        while abs(rest) > 0.5:
            d = max(-90, min(90, rest))
            parts.append(d)
            rest -= d
    for d in parts:
        _orbit_drag(s, d * PT_PER_DEG * s.scale)
    time.sleep(0.3)
    return shot(f"cam-{int(deg)}")


def zoom_camera(ticks: int, s: Shot | None = None) -> Shot:
    """Wheel over the viewport. Measured (silhouette shrinking/growing):
    positive ticks (scroll down) zoom OUT, negative zoom IN; one tick is a
    small step."""
    s = s or shot("zoom")
    I.scroll(ticks, *_vp_center(s), shot=s)
    time.sleep(0.6)
    return shot("zoomed")


def _silhouette(s: Shot):
    """The model = the large blobs that differ from the viewport background
    (drops UI markers such as the small blue light badge)."""
    import cv2
    import numpy as np
    vp = box(s, VIEWPORT)
    vp = (vp[0], vp[1] + int(0.01 * s.size[1]), vp[2], vp[3])   # skip the tab underline
    a = np.asarray(s.image.crop(vp).convert("RGB")).astype(int)
    # background = VRoid's flat light-grey viewport (#EAEAEA-ish); take the
    # most common border colour and insist it is that grey - in an extreme
    # close-up the border is the model itself and nothing can be measured
    border = np.concatenate([a[:8].reshape(-1, 3), a[-8:].reshape(-1, 3),
                             a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    if bg.min() < 215 or bg.max() - bg.min() > 12 or (np.abs(border - bg).max(axis=1) < 10).mean() < 0.6:
        return None
    m = (np.abs(a - bg).max(axis=2) > 25).astype(np.uint8)
    m = cv2.dilate(m, np.ones((9, 9), np.uint8))         # bridge dotted outlines
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    # a white shirt on the light-grey background splits the body into
    # separate blobs (head / legs): merge every blob of real size, so small
    # UI markers are still dropped
    areas = st[1:, cv2.CC_STAT_AREA]
    keep = [1 + i for i in range(n - 1) if areas[i] >= 0.03 * areas.max()]
    x0 = min(st[k][0] for k in keep)
    y0 = min(st[k][1] for k in keep)
    x1 = max(st[k][0] + st[k][2] for k in keep)
    y1 = max(st[k][1] + st[k][3] for k in keep)
    return {"w": int(x1 - x0), "x0": int(x0 + vp[0]), "x1": int(x1 + vp[0]),
            "y0": int(y0 + vp[1]), "y1": int(y1 + vp[1]), "mask": np.isin(lab, keep), "vp": vp}


def _skin_in_head(s: Shot) -> float:
    """Skin-coloured fraction of the face band (5-13% of body height, centre
    third) - high when the face is towards the camera."""
    import numpy as np
    sil = _silhouette(s)
    if sil is None:
        return 0.0
    vp = sil["vp"]
    a = np.asarray(s.image.crop(vp).convert("RGB")).astype(int)
    y0, y1 = sil["y0"] - vp[1], sil["y1"] - vp[1]
    hgt = y1 - y0
    cx = (sil["x0"] + sil["x1"]) // 2 - vp[0]
    band = a[y0 + int(hgt * 0.05): y0 + int(hgt * 0.13), max(0, cx - int(hgt * 0.04)): cx + int(hgt * 0.04)]
    r, g, b = band[..., 0], band[..., 1], band[..., 2]
    skin = (r > 200) & (g > 165) & (b > 140) & (r > b + 10) & (r - g < 50)
    return float(skin.mean()) if band.size else 0.0


def _symmetry(s: Shot) -> float:
    """Mirror IoU of the silhouette about its own centre: 1.0 = perfectly
    symmetric (front or back view of a T-posed model)."""
    import numpy as np
    sil = _silhouette(s)
    if sil is None:
        return 0.0
    vp = sil["vp"]
    m = sil["mask"][sil["y0"] - vp[1]:sil["y1"] - vp[1], sil["x0"] - vp[0]:sil["x1"] - vp[0]]
    f = m[:, ::-1]
    return float((m & f).sum()) / max(1, (m | f).sum())


def face_front() -> Shot:
    """Orbit until the model faces the camera.

    Measured: the widest silhouette is NOT the front (a 3/4 view of the
    slightly forward arms is wider), but front/back are the only views with
    a near-perfect left-right mirror symmetry. So: coarse 30-degree sweep on
    symmetry (ignoring narrow side views), refine at 5 then 2 degrees, then
    choose front over back by visible face skin."""
    s = shot("front-search")
    samples = []
    for k in range(12):
        sil = _silhouette(s)
        samples.append((k * 30, sil["w"] if sil else 0, _symmetry(s)))
        S.log("front-search", angle=k * 30, w=samples[-1][1], sym=round(samples[-1][2], 3))
        s = rotate_camera(30, s)
    cur = 0                                   # 12 x 30 = 360: back at the start angle
    wmax = max(w for _, w, _ in samples)
    target = max((x for x in samples if x[1] >= 0.75 * wmax), key=lambda x: x[2])[0]

    def go(a):
        nonlocal s, cur
        d = ((a - cur + 180) % 360) - 180
        if d:
            s = rotate_camera(d, s)
        cur = a

    for step in (5, 2):
        best = None
        for off in (-2 * step, -step, 0, step, 2 * step):
            go(target + off)
            v = _symmetry(s)
            if best is None or v > best[0]:
                best = (v, target + off)
        target = best[1]
    go(target)
    f = _skin_in_head(s)
    go(target + 180)
    if _skin_in_head(s) <= f:
        go(target)
    S.log("face_front", angle=cur, sym=round(_symmetry(s), 3))
    return s


FIT_OK = {"ok": True}


def fit_full_body(max_steps: int = 40) -> Shot:
    """Zoom so the whole model fills ~60-90% of the viewport height.
    One wheel tick is a ~0.5% zoom step, so zoom out in bursts of 15.
    Sets FIT_OK['ok'] = False if it gives up (callers report it)."""
    FIT_OK["ok"] = True
    s = shot("fit")
    for _ in range(max_steps):
        sil = _silhouette(s)
        vp = box(s, VIEWPORT)
        vh = vp[3] - vp[1]
        if sil is None:
            s = zoom_camera(15, s)
            continue
        inside = sil["y1"] < vp[3] - 15 and sil["y0"] > vp[1] + 25
        frac_h = (sil["y1"] - sil["y0"]) / vh
        if not inside:
            s = zoom_camera(15, s)          # out
        elif frac_h < 0.6:
            s2 = zoom_camera(-2, s)         # in, gently; undo if it overshoots
            sil2 = _silhouette(s2)
            if sil2 is None or sil2["y1"] >= vp[3] - 15 or sil2["y0"] <= vp[1] + 25:
                return zoom_camera(2, s2)
            s = s2
        else:
            return s
    FIT_OK["ok"] = False
    S.log("warn", msg="fit_full_body gave up - model may be cropped", shot=str(s.path))
    return s


def capture_views(tag: str = "views") -> dict:
    """Front / side (+90) / back / side (+270) crops of the viewport plus a
    combined sheet, for comparing the model with the brief."""
    from PIL import Image
    if current_screen()["tab"] in (None, "body", "accessories"):
        open_tab("outfit")   # 体型 draws a height-gauge plane, アクセサリー gizmos: both spoil silhouettes
    fit_full_body()      # a clipped silhouette makes the width search meaningless
    face_front()
    names = [("front", 0), ("side_90", 90), ("back", 90), ("side_270", 90)]
    crops, paths = [], {}
    s = shot("view")
    for name, d in names:
        if d:
            s = rotate_camera(d, s)      # no re-zoom here: zoom moves the orbit pivot
        sil = _silhouette(s)
        vp = box(s, VIEWPORT)
        if sil:
            pad = int(0.03 * s.size[1])
            cx = (sil["x0"] + sil["x1"]) // 2
            half = max(sil["y1"] - sil["y0"], 10) * 0.42
            c = s.image.crop((int(max(vp[0], cx - half)), max(vp[1], sil["y0"] - pad),
                              int(min(vp[2], cx + half)), min(vp[3], sil["y1"] + pad)))
        else:
            c = s.image.crop(vp)
        p = S.capture_path(f"{tag}-{name}")
        c.save(p)
        paths[name] = str(p)
        crops.append(c)
    h = max(c.height for c in crops)
    crops = [c.resize((int(c.width * h / c.height), h)) for c in crops]
    sheet = Image.new("RGB", (sum(c.width for c in crops), h), "white")
    x = 0
    for c in crops:
        sheet.paste(c, (x, 0))
        x += c.width
    sp = S.capture_path(f"{tag}-sheet")
    sheet.save(sp)
    rotate_camera(90, s)                       # leave the camera facing front again
    paths["sheet"] = str(sp)
    paths["full_body_fit"] = FIT_OK["ok"]
    S.log("capture_views", **paths)
    return paths


# --- model lifecycle ---------------------------------------------------------------

BASES = {"female": ["女性", "Fem", "Female"], "male": ["男性", "Masc", "Male"]}


def to_start_screen() -> Shot:
    s = shot("where")
    scr = current_screen(s)["screen"]
    if scr == "start":
        return s
    if scr in ("texture_editor", "export"):
        close_texture_editor() if scr == "texture_editor" else close_subeditor()
    menu_item("back_to_models")           # refuses when dirty
    s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "start", timeout=30, tag="start")
    if not ok:
        raise RuntimeError(f"start screen did not appear; see {s.path}")
    return s


def create_character(base: str = "female") -> dict:
    """Start screen > 新規作成 > ベースの選択 (女性/男性) > editor."""
    names = BASES["male" if base.lower().startswith(("m", "男")) else "female"]
    s = to_start_screen()
    m = L.find_text(s, ["新規作成", "Create New"], region=box(s, (0, 0.1, 0.3, 0.4)))
    if m is None:
        raise RuntimeError(f"no 新規作成 card; see {s.path}")
    cap = None
    for _ in range(3):                      # the first click on the start screen is sometimes eaten
        I.click(m.center[0], m.center[1] - int(0.09 * s.size[1]), shot=s)   # the + card above its caption
        s, cap = wait_for(lambda sh: L.find_text(sh, names, exact=True, region=box(sh, (0.3, 0.3, 0.7, 0.8))),
                          timeout=6, tag="base-select")
        if cap:
            break
    if not cap:
        raise RuntimeError(f"base selection did not offer {names}; see {s.path}")
    I.click(cap.center[0], cap.center[1] - int(0.08 * s.size[1]), shot=s)   # thumbnail above caption
    s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "editor", timeout=90, poll=2, tag="new-model")
    if not ok:
        raise RuntimeError(f"editor did not open; see {s.path}")
    time.sleep(3)
    s = shot("new-model-ready")
    r = {"base": names[0], "title": project_title(), "shot": str(s.path)}
    S.log("create_character", **r)
    return r


def open_character(path: str) -> dict:
    """Start screen > 開く > native open panel (Go to Folder with the full path)."""
    p = Path(path).expanduser().resolve()
    if not p.exists() or p.suffix.lower() != ".vroid":
        raise FileNotFoundError(f"{p} is not an existing .vroid file")
    if re.search(r"[^A-Za-z0-9._/-]", str(p)):
        raise ValueError("path must be plain ASCII [A-Za-z0-9._/-] to be typed into the native panel")
    s = to_start_screen()
    m = L.find_text(s, ["開く", "Open"], exact=True, region=box(s, (0.85, 0.08, 1, 0.16)))
    if m is None:
        raise RuntimeError(f"no 開く button; see {s.path}")
    I.click(*m.center, shot=s)
    time.sleep(2.0)
    I.ascii_input_source()
    I.key("cmd+shift+g", allow_panel=True)
    time.sleep(1.2)
    _panel_field(str(p))
    time.sleep(0.8)
    I.key("return", allow_panel=True)      # go to the file
    time.sleep(1.2)
    I.key("return", allow_panel=True)      # open it
    s, ok = wait_for(lambda sh: current_screen(sh)["screen"] == "editor", timeout=90, poll=2, tag="opened")
    if not ok:
        raise RuntimeError(f"model did not open; see {s.path}")
    r = {"path": str(p), "title": project_title(), "shot": str(s.path)}
    S.log("open_character", **r)
    return r


# --- hair colour ------------------------------------------------------------------

HAIR_PARTS = ["前髪", "後髪", "一体型", "つけ髪", "横髪", "アホ毛", "はね毛", "ベースヘアー"]


def change_hair_color(main: str, highlight: str | None = None, parts: list[str] | None = None) -> list[dict]:
    """Set メインカラー/ハイライトカラー on each hair part that shows them.
    Parts with no hair selected show no colour rows and are skipped."""
    out = []
    open_tab("hair")
    for part in parts or HAIR_PARTS:
        try:
            open_category(part)
        except RuntimeError as e:
            out.append({"part": part, "skipped": str(e)})
            continue
        s = shot(f"hair-{part}")
        if not L.find_text(s, ["メインカラー", "Main Color"], region=box(s, PANEL_RIGHT)):
            out.append({"part": part, "skipped": "no colour rows (part empty?)"})
            continue
        rec = {"part": part, "main": set_hex_row(["メインカラー", "Main Color"], main)["ok"]}
        if highlight:
            rec["highlight"] = set_hex_row(["ハイライトカラー", "Highlight Color"], highlight)["ok"]
        out.append(rec)
    S.log("change_hair_color", result=out)
    return out
