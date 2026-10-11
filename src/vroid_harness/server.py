"""vroid-harness MCP server (stdio).

Claude is the eyes (screenshots come back as images), these tools are the
hands. Coordinates default to `space="image"`: pixels of the full-resolution
window capture. Screenshots are downscaled for transport; every screenshot
result states `downscale`, and `space="shown"` accepts coordinates read off
the image you were shown.
"""
from __future__ import annotations

import functools
import json
import time
import traceback

from mcp.server.fastmcp import FastMCP, Image

from . import capture as C
from . import input as I
from . import locate as L
from . import macos as M
from . import session as S
from . import vroid as V
from . import spec as SP

mcp = FastMCP("vroid-harness")


def _shot_payload(s: C.Shot, note: dict | None = None, max_px: int | None = None):
    data, f = C.for_transport(s.image, max_px)
    s.meta["downscale"] = f
    info = {"path": str(s.path), "image_size": list(s.size), "downscale": round(f, 4),
            "scale_px_per_pt": s.scale, "window_origin_pt": list(s.origin),
            "steps_used": S.S.steps, "step_budget": S.S.budget}
    if note:
        info.update(note)
    return [json.dumps(info, ensure_ascii=False), Image(data=data, format="png")]


def tool(fn):
    """Register + log + turn exceptions into readable errors with a recovery hint."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        S.log("tool", name=fn.__name__, args=kw)
        try:
            return fn(*a, **kw)
        except S.BudgetExceeded as e:
            return f"STOPPED: {e}"
        except I.GuardError as e:
            return f"REFUSED (focus guard): {e}"
        except Exception as e:
            S.log("error", name=fn.__name__, error=repr(e), tb=traceback.format_exc()[-1500:])
            return (f"ERROR in {fn.__name__}: {e}\n"
                    "Recovery: take vroid_screenshot, look at what is actually on screen "
                    "(modal? wrong tab? scrolled?) before retrying - do not repeat the same click blindly.")
    return mcp.tool()(wrapper)


# --- stage 1: eyes and hands ----------------------------------------------------

@tool
def vroid_status() -> str:
    """Permissions, process, window geometry/on-screen state, focus, displays,
    work folders and step budget. Read-only; sends no input."""
    w = M.main_window()
    return json.dumps({
        "permissions": M.permissions(),
        "running": M.vroid_pid() is not None,
        "window": w.__dict__ if w else None,
        "window_fully_on_screen": M.window_on_screen(w) if w else None,
        "frontmost": M.front_owner(),
        "vroid_is_front": M.is_vroid_front(),
        "displays": M.displays(),
        "home": str(S.HOME), "log": str(S.LOG_FILE),
        "steps_used": S.S.steps, "step_budget": S.S.budget,
    }, ensure_ascii=False, indent=1)


@tool
def vroid_launch(fit: bool = True) -> str:
    """Start VRoid Studio if needed, move its window fully onto a display
    (fit=True) and bring it to the front."""
    M.launch()
    if fit and not M.window_on_screen():
        M.fit_window()
    ok = M.activate()
    w = M.main_window()
    return json.dumps({"front": ok, "window": w.__dict__ if w else None,
                       "fully_on_screen": M.window_on_screen(w)}, ensure_ascii=False)


@tool
def vroid_fit_window(screen: int | None = None, width: float | None = None,
                     height: float | None = None) -> str:
    """Move/resize the VRoid window into a screen's visible area (index from
    vroid_status displays order of NSScreen; None = the screen it is mostly on)."""
    w = M.fit_window(screen, width, height)
    return json.dumps({"window": w.__dict__, "fully_on_screen": M.window_on_screen(w)})


@tool
def vroid_activate() -> str:
    """Bring VRoid Studio to the front."""
    return json.dumps({"front": M.activate(), "frontmost": M.front_owner()}, ensure_ascii=False)


@tool
def vroid_screenshot(tag: str = "", max_px: int | None = None, panel: bool = False):
    """Capture the VRoid window (or the open/save panel with panel=True) and
    return it as an image. Coordinates in other tools default to *image px*
    of this capture; multiply what you read off the shown picture by
    1/downscale, or pass space='shown'."""
    s = C.grab_panel(tag or "panel") if panel else C.grab_window(tag)
    if s is None:
        return "no open/save panel visible"
    return _shot_payload(s, max_px=max_px)


@tool
def vroid_zoom(x0: int, y0: int, x1: int, y1: int, factor: float = 2.0, fresh: bool = True):
    """Enlarged crop of a region (image px) for reading small UI text/icons."""
    s = C.grab_window("zoom") if fresh or C.last() is None else C.last()
    img = C.zoom(s, (x0, y0, x1, y1), factor)
    data, f = C.for_transport(img)
    return [json.dumps({"region": [x0, y0, x1, y1], "zoom": factor, "downscale": f}),
            Image(data=data, format="png")]


@tool
def vroid_find_text(query: str, region: list[int] | None = None, exact: bool = False) -> str:
    """OCR (Apple Vision, ja+en) a fresh capture; return matching lines with
    centres in image px. `query` may be 'A|B' for synonyms (e.g. '衣装|Outfit')."""
    s = C.grab_window("find")
    hits = L.find_text(s, query.split("|"), exact=exact, all_matches=True,
                       region=tuple(region) if region else None)
    return json.dumps({"shot": str(s.path), "matches": [h.as_dict() for h in hits]},
                      ensure_ascii=False)


@tool
def vroid_read_screen(region: list[int] | None = None) -> str:
    """All OCR text lines with boxes (image px). Use to check state without an image."""
    s = C.grab_window("read")
    return json.dumps({"shot": str(s.path), "size": s.size,
                       "lines": [m.as_dict() for m in L.ocr(s, region=tuple(region) if region else None)]},
                      ensure_ascii=False)


def _verify(before: C.Shot, tag: str):
    time.sleep(0.6)
    after = C.grab_window(tag)
    W, H = after.size
    ui = L.ui_diff(before, after, (0, 0, W, int(H * 0.07)))
    left = L.ui_diff(before, after, V.box(after, (0, 0.07, 0.172, 1)))
    right = L.ui_diff(before, after, V.box(after, V.PANEL_RIGHT))
    return after, {"changed": {"top": round(ui, 4), "left": round(left, 4), "right": round(right, 4)}}


def _last_or_new():
    return C.last() or C.grab_window("ref")


@tool
def vroid_click(x: float, y: float, space: str = "image", button: str = "left",
                count: int = 1, modifiers: str = "", verify: bool = True):
    """Click at (x, y). space: image (default) | shown | points. count=2 for a
    double click. Returns an after-screenshot plus how much the UI changed."""
    ref = _last_or_new()
    I.click(x, y, space=space, shot=ref, button=button, count=count, modifiers=modifiers)
    if not verify:
        return "ok"
    after, note = _verify(ref, "after-click")
    return _shot_payload(after, note)


@tool
def vroid_drag(x1: float, y1: float, x2: float, y2: float, space: str = "image",
               button: str = "left", verify: bool = True):
    """Press-glide-release. Right-drag orbits the camera in the viewport,
    middle-drag pans."""
    ref = _last_or_new()
    I.drag(x1, y1, x2, y2, space=space, shot=ref, button=button)
    if not verify:
        return "ok"
    after, note = _verify(ref, "after-drag")
    return _shot_payload(after, note)


@tool
def vroid_scroll(dy: int, x: float | None = None, y: float | None = None,
                 space: str = "image", dx: int = 0, verify: bool = True):
    """Wheel `dy` lines (positive = scroll content down) with the pointer at
    (x, y) - park it over the panel you mean."""
    ref = _last_or_new()
    I.scroll(dy, x, y, space=space, shot=ref, dx=dx)
    if not verify:
        return "ok"
    after, note = _verify(ref, "after-scroll")
    return _shot_payload(after, note)


@tool
def vroid_type(text: str, mode: str = "unicode", clear_first: bool = False,
               submit: bool = False) -> str:
    """Type into the focused field. mode: unicode | keys (ascii/digits via
    keycodes, safest for numeric boxes) | paste (clipboard, restored)."""
    if clear_first:
        I.clear_field()
    I.type_text(text, mode=mode)
    if submit:
        I.key("return")
    return "ok"


@tool
def vroid_key(combo: str, times: int = 1) -> str:
    """Key or shortcut: 'return', 'escape', 'tab', 'cmd+z', 'cmd+s', 'cmd+shift+s'."""
    I.key(combo, times)
    return "ok"


@tool
def vroid_reset_budget(extra_steps: int = 300) -> str:
    """Raise the input-step ceiling. Only after re-observing and confirming the
    plan is making progress."""
    S.S.budget = S.S.steps + max(1, min(extra_steps, 2000))
    S.log("budget", budget=S.S.budget)
    return f"budget now {S.S.budget} (used {S.S.steps})"


# --- stage 2: VRoid operations -----------------------------------------------------

@tool
def vroid_current_screen() -> str:
    """Which screen VRoid shows (editor/start/export/hair_editor/...), the
    active tab and the left panel title."""
    return json.dumps(V.current_screen(), ensure_ascii=False)


@tool
def vroid_open_editor_tab(name: str):
    """Open an editor tab: face|hair|body|outfit|accessories|look (or 顔/髪型/
    体型/衣装/アクセサリー/ルック). Verifies the tab became active."""
    return _shot_payload(V.open_tab(name), {"tab": V.tab_key(name)})


@tool
def vroid_list_categories(tab: str | None = None) -> str:
    """Click through every left-rail icon of a tab and report each panel
    title - the ground truth of which sub-categories exist."""
    return json.dumps(V.list_categories(tab), ensure_ascii=False, indent=1)


@tool
def vroid_open_category(name: str, tab: str | None = None):
    """Select a left-rail sub-category by its panel title."""
    return _shot_payload(V.open_category(name, tab), {"category": name})


@tool
def vroid_list_parameters() -> str:
    """Scroll the right panel and return every row label with its current
    numeric value (and hex for colour rows)."""
    return json.dumps(V.list_parameters(), ensure_ascii=False, indent=1)


@tool
def vroid_find_parameter(label: str) -> str:
    """Scroll the right panel to a row and report its position and value."""
    s, m = V.find_parameter(label)
    return json.dumps({"label": m.text, "center": m.center, "value": V.read_value(s, m),
                       "shot": str(s.path)}, ensure_ascii=False)


@tool
def vroid_set_parameter(label: str, value: float):
    """Type an exact value into a slider row's numeric box, then read it back
    (ok=false if VRoid clamped or ignored it)."""
    r = V.set_parameter(label, value)
    return _shot_payload(C.last(), r)


@tool
def vroid_set_hex_row(label: str, hex: str):
    """Right-panel colour row with a '#RRGGBB' box (e.g. hair メインカラー /
    ハイライトカラー). Verified by OCR read-back."""
    r = V.set_hex_row(label.split("|"), hex)
    return _shot_payload(C.last(), r)


@tool
def vroid_change_hair_color(main: str, highlight: str | None = None,
                            parts: list[str] | None = None) -> str:
    """Set メインカラー (and ハイライトカラー) on every hair part that has
    them (髪型 tab: 前髪, 後髪, 横髪, ... ベースヘアー), or only `parts`."""
    return json.dumps(V.change_hair_color(main, highlight, parts), ensure_ascii=False, indent=1)


@tool
def vroid_change_eye_color(hex: str, right: str | None = None) -> str:
    """顔 > 瞳 > テクスチャを編集 > カラー調整: enables colour adjustment and
    sets left (and right) iris colour."""
    return json.dumps(V.change_eye_color(hex, right), ensure_ascii=False)


@tool
def vroid_open_texture_editor():
    """Open テクスチャを編集 for the current category (outfit item, iris...)."""
    return _shot_payload(V.open_texture_editor())


@tool
def vroid_list_material_colors() -> str:
    """In the texture editor: every material section with its シェーダー
    カラー 基本色 (base) / かげ色 (shade). Use the indices with
    vroid_change_outfit_color."""
    return json.dumps(V.list_shader_colors(), ensure_ascii=False, indent=1)


@tool
def vroid_change_outfit_color(base: str, shade: str | None = None,
                              sections: list[int] | None = None, match: str | None = None,
                              category: str | None = None, close: bool = True) -> str:
    """Recolour an outfit item via シェーダーカラー (multiplies its texture, so
    light/white textures take the colour best). category (e.g. トップス,
    ボトムス, 靴) opens it first; sections/match pick materials (see
    vroid_list_material_colors); shade defaults to a darker base. The edit
    stays on the model (closing chooses 保存せずに編集を終了 = no closet item)."""
    if category:
        V.open_category(category, tab="outfit")
        V.open_texture_editor()
    elif V.current_screen()["screen"] != "texture_editor":
        V.open_texture_editor()
    r = V.set_shader_colors(base, shade, sections, match)
    if close:
        V.close_texture_editor()
    return json.dumps(r, ensure_ascii=False, indent=1)


@tool
def vroid_close_texture_editor(save_item: bool = False):
    """Leave the texture editor (default: keep edits on the model, do not
    store a closet item)."""
    return _shot_payload(V.close_texture_editor(save_item))


@tool
def vroid_scan_presets(category: str | None = None, tab: str | None = None):
    """Number every tile of the current preset grid (optionally open
    tab/category first) and return a contact sheet image - choose by eye,
    then call vroid_select_preset(index). Index 0 is usually 'none'."""
    if category:
        V.open_category(category, tab=tab)
    items, p = V.scan_presets()
    from PIL import Image as PI
    img = PI.open(p)
    data, f = C.for_transport(img)
    return [json.dumps({"tiles": len(items), "sheet": str(p)}), Image(data=data, format="png")]


@tool
def vroid_select_preset(index: int, category: str | None = None, tab: str | None = None):
    """Click preset tile `index` (from vroid_scan_presets) and confirm the
    model changed."""
    if category:
        V.open_category(category, tab=tab)
    r = V.select_preset(index)
    return _shot_payload(C.last(), r)


@tool
def vroid_select_hair_style(index: int):
    """髪型 > 髪セット preset `index` (see vroid_scan_presets(category='髪セット', tab='hair'))."""
    V.open_category("髪セット", tab="hair")
    return _shot_payload(C.last(), V.select_preset(index))


@tool
def vroid_select_outfit(category: str, index: int):
    """衣装 tab: category (全身セット/トップス/ボトムス/ワンピース/首飾り/腕飾り/
    上半身インナー/下半身インナー/レッグウェア/靴) preset `index`."""
    V.open_category(category, tab="outfit")
    return _shot_payload(C.last(), V.select_preset(index))


@tool
def vroid_edit_body_proportions(values: dict[str, float]) -> str:
    """体型 tab: {label: value} typed into numeric boxes, each read back.
    Labels as shown in the UI (e.g. 頭の大きさ, 脚の長さ, 身長の高さ（女性）)."""
    V.open_tab("body")
    return json.dumps([V.set_parameter(k, v) for k, v in values.items()], ensure_ascii=False, indent=1)


@tool
def vroid_rotate_camera(deg: float):
    """Orbit the viewport camera by deg (right-drag; 2.5 pt per degree)."""
    return _shot_payload(V.rotate_camera(deg))


@tool
def vroid_capture_character_views(tag: str = "views"):
    """Front / side / back / side views (auto-aligned to the front, full
    body) as one sheet image for judging the model against the brief."""
    r = V.capture_views(tag)
    from PIL import Image as PI
    data, f = C.for_transport(PI.open(r["sheet"]), 2000)
    return [json.dumps(r), Image(data=data, format="png")]


@tool
def vroid_save_project(name: str | None = None) -> str:
    """name given: Save As into the work folder projects/<name>.vroid (never
    overwrites: -2, -3 suffixes). No name: Cmd+S on an already-named project."""
    r = V.save_project_as(name) if name else V.save_project()
    return json.dumps(r, ensure_ascii=False)


@tool
def vroid_export_vrm(name: str, avatar_name: str, author: str, version: str = "1.0",
                     fmt: str = "1.0") -> str:
    """Export exports/<name>.vrm (VRM1.0 default, fmt='0.0' for VRM0.0) with
    metadata; licence options stay at VRoid defaults. Verifies the file
    exists, its size and glTF magic. Returns to the editor afterwards."""
    r = V.export_vrm(name, avatar_name, author, version, fmt)
    V.close_subeditor()
    return json.dumps(r, ensure_ascii=False)


@tool
def vroid_create_character(base: str = "female") -> str:
    """Start a new model from VRoid's base (female/male). Unsaved changes in
    the open model are protected: refuses if the title shows '*'."""
    return json.dumps(V.create_character(base), ensure_ascii=False)


@tool
def vroid_open_character(path: str) -> str:
    """Open a .vroid file via the start screen's 開く (native open panel)."""
    return json.dumps(V.open_character(path), ensure_ascii=False)


# --- stage 4/5: specs -----------------------------------------------------------

@tool
def vroid_validate_spec(spec: dict) -> str:
    """Check a character spec (see spec.py docstring / docs/spec-example.json)."""
    errs = SP.validate(spec)
    return json.dumps({"ok": not errs, "errors": errs}, ensure_ascii=False)


@tool
def vroid_apply_spec(spec: dict, new: bool = True, export: bool = True, views: bool = True) -> str:
    """Build a character from a spec: new model -> save-as -> hair preset/colour
    -> eyes -> outfit full set / items / shader colours -> body -> four views
    -> VRM export, with a save checkpoint after each stage. Preset indices
    must come from vroid_scan_presets contact sheets you have looked at."""
    r = SP.apply_spec(spec, new=new, export=export, views=views)
    return json.dumps(r, ensure_ascii=False, indent=1, default=str)


@tool
def vroid_apply_specs(specs: list[dict]) -> str:
    """Several characters in sequence (stops at the first failure)."""
    return json.dumps(SP.apply_specs(specs), ensure_ascii=False, indent=1, default=str)


@mcp.prompt()
def build_character(brief: str) -> str:
    """The autonomous build loop for one character from a natural-language brief."""
    return f"""Build a VRoid Studio character for this brief:

{brief}

Loop (stop and ask if confidence is low; never click blindly twice):
1. Understand: write a spec JSON (name, base, hair, eyes, outfit, body, export).
   List anything VRoid presets cannot express under "unsupported" with the reason.
2. Observe: vroid_status, vroid_current_screen. Untitled/dirty projects are never discarded.
3. Choose presets by eye: vroid_scan_presets(category='髪セット', tab='hair'),
   (category='全身セット'|'トップス'|'ボトムス'|'靴'|..., tab='outfit'). Put the indices in the spec.
4. Act: vroid_apply_spec(spec, export=False) - or the single tools step by step.
5. Verify: vroid_capture_character_views; compare front/side/back with the brief.
6. Fix differences with the single tools (vroid_select_preset, vroid_change_outfit_color,
   vroid_set_parameter, vroid_change_hair_color, vroid_change_eye_color), then re-capture.
7. When it matches: vroid_save_project(), vroid_export_vrm(...). Report the paths and
   any unsupported items.
On any ERROR/REFUSED: vroid_screenshot first, reason about the actual state, then recover.
"""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
