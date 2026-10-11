"""Mouse and keyboard through CGEvent (kCGHIDEventTap), behind a focus guard.

Every public function here:
  1. spends one step of the budget,
  2. re-checks that VRoid Studio is the frontmost app (and that the target
     point is inside its window) - otherwise raises GuardError, sends nothing,
  3. posts the events and logs them.

Events are posted at the HID tap rather than to a PID: Unity apps read the
real event stream, and PID-targeted events are frequently ignored by them.
"""
from __future__ import annotations

import os
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString

from . import macos as M
from . import session as S
from .capture import Shot

AUTO_ACTIVATE = os.environ.get("VROID_HARNESS_AUTO_ACTIVATE", "1") != "0"


class GuardError(RuntimeError):
    pass


def guard(allow_panel: bool = False) -> None:
    perms = M.permissions()
    if not perms["accessibility"]:
        raise GuardError("Accessibility permission missing for the host terminal: System "
                         "Settings > Privacy & Security > Accessibility.")
    if M.is_vroid_front(allow_panel=allow_panel):
        return
    if AUTO_ACTIVATE and not (allow_panel and M.panel_window()):
        if M.activate():
            return
    raise GuardError(f"frontmost app is {M.front_owner()!r}, not VRoid Studio - input refused")


def _check_point(px: float, py: float, allow_panel: bool) -> None:
    rects = []
    w = M.main_window()
    if w:
        rects.append(w.bounds)
    if allow_panel:
        p = M.panel_window()
        if p:
            rects.append(p.bounds)
    for x, y, ww, hh in rects:
        if x <= px < x + ww and y <= py < y + hh:
            return
    raise GuardError(f"point ({px:.0f},{py:.0f}) is outside the VRoid window - refused")


def resolve(x: float, y: float, space: str, shot: Shot | None) -> tuple[float, float]:
    """Convert to global points. space: image | shown | points."""
    if space == "points":
        return float(x), float(y)
    if shot is None:
        raise ValueError("a Shot is required for image/shown coordinates")
    if space == "shown":
        f = shot.meta.get("downscale", 1.0) or 1.0
        x, y = x / f, y / f
    return shot.to_points(x, y)


# --- mouse -------------------------------------------------------------------

_BTN = {
    "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp,
             Quartz.kCGEventLeftMouseDragged, Quartz.kCGMouseButtonLeft),
    "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp,
              Quartz.kCGEventRightMouseDragged, Quartz.kCGMouseButtonRight),
    "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp,
               Quartz.kCGEventOtherMouseDragged, Quartz.kCGMouseButtonCenter),
}


def _post(ev) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _mouse(kind, x, y, button=Quartz.kCGMouseButtonLeft, clicks: int = 0, flags: int = 0):
    ev = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), button)
    if clicks:
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, clicks)
    if flags:
        Quartz.CGEventSetFlags(ev, flags)
    _post(ev)


def _cursor() -> tuple[float, float]:
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return loc.x, loc.y


class _Pinned:
    """Detach the physical mouse from the cursor while we act, so a human
    nudging the mouse cannot move the pointer between our move and press."""
    def __enter__(self):
        Quartz.CGAssociateMouseAndMouseCursorPosition(False)
        return self

    def __exit__(self, *exc):
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)


def _arrive(x: float, y: float) -> None:
    """Warp exactly onto (x, y) and emit a move there so hover states fire.

    Unity routes wheel/clicks by pointer *movement*: if the cursor is already
    on the target, a zero-delta move is ignored and the next scroll can be
    dropped. So always approach from a few points away."""
    cx, cy = _cursor()
    if abs(cx - x) < 4 and abs(cy - y) < 4:
        _mouse(Quartz.kCGEventMouseMoved, x + 6, y + 6)
        time.sleep(0.03)
    _glide(x, y, steps=6)
    Quartz.CGWarpMouseCursorPosition((x, y))
    _mouse(Quartz.kCGEventMouseMoved, x, y)
    time.sleep(0.12)
    cx, cy = _cursor()
    if abs(cx - x) > 2 or abs(cy - y) > 2:
        Quartz.CGWarpMouseCursorPosition((x, y))
        _mouse(Quartz.kCGEventMouseMoved, x, y)
        time.sleep(0.1)


def _glide(x: float, y: float, steps: int = 6, dragged=None, button=0) -> None:
    sx, sy = _cursor()
    for i in range(1, steps + 1):
        t = i / steps
        _mouse(dragged or Quartz.kCGEventMouseMoved, sx + (x - sx) * t, sy + (y - sy) * t, button)
        time.sleep(0.012)


def move(x, y, space="image", shot=None):
    S.spend("move")
    px, py = resolve(x, y, space, shot)
    guard()
    _check_point(px, py, False)
    _glide(px, py)
    S.log("move", x=x, y=y, space=space, points=(px, py))


def click(x, y, space="image", shot: Shot | None = None, button="left", count: int = 1,
          modifiers: str = "", allow_panel: bool = False) -> tuple[float, float]:
    S.spend("click")
    px, py = resolve(x, y, space, shot)
    guard(allow_panel)
    _check_point(px, py, allow_panel)
    down, up, _, btn = _BTN[button]
    flags = _flags(modifiers)
    with _Pinned():
        _arrive(px, py)
        for c in range(1, count + 1):
            _mouse(down, px, py, btn, c, flags)
            time.sleep(0.09)   # Unity samples button state per frame; too short a press is lost
            _mouse(up, px, py, btn, c, flags)
            time.sleep(0.07)
    S.log("click", x=x, y=y, space=space, points=(round(px, 1), round(py, 1)),
          button=button, count=count, modifiers=modifiers,
          shot=str(shot.path) if shot else None)
    return px, py


def drag(x1, y1, x2, y2, space="image", shot: Shot | None = None, button="left",
         steps: int = 24, hold: float = 0.08) -> None:
    S.spend("drag")
    p1 = resolve(x1, y1, space, shot)
    p2 = resolve(x2, y2, space, shot)
    guard()
    _check_point(*p1, False)
    _check_point(*p2, False)
    down, up, dragged, btn = _BTN[button]
    with _Pinned():
        _arrive(*p1)
        _mouse(down, *p1, btn, 1)
        time.sleep(hold)
        for i in range(1, steps + 1):
            t = i / steps
            x, y = p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t
            Quartz.CGWarpMouseCursorPosition((x, y))
            _mouse(dragged, x, y, btn)
            time.sleep(0.015)
        time.sleep(hold)
        _mouse(up, *p2, btn, 1)
    S.log("drag", frm=(x1, y1), to=(x2, y2), space=space, button=button)


def scroll(dy: int, x=None, y=None, space="image", shot: Shot | None = None, dx: int = 0) -> None:
    """dy > 0 scrolls *down* the content (towards the end of a list)."""
    S.spend("scroll")
    guard()
    if x is not None and y is not None:
        px, py = resolve(x, y, space, shot)
        _check_point(px, py, False)
        with _Pinned():
            _arrive(px, py)
    sign = -1 if dy > 0 else 1
    for _ in range(abs(int(dy))):
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, sign, 0)
        _post(ev)
        time.sleep(0.02)
    if dx:
        sgn = -1 if dx > 0 else 1
        for _ in range(abs(int(dx))):
            _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 0, sgn))
            time.sleep(0.02)
    S.log("scroll", dy=dy, dx=dx, x=x, y=y, space=space)


# --- keyboard ----------------------------------------------------------------

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "`": 50, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53, "forwarddelete": 117, "home": 115,
    "end": 119, "pageup": 116, "pagedown": 121, "left": 123, "right": 124,
    "down": 125, "up": 126, "kp_enter": 76,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "eisu": 102, "kana": 104,
}
_MODS = {
    "cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift, "alt": Quartz.kCGEventFlagMaskAlternate,
    "option": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl,
    "control": Quartz.kCGEventFlagMaskControl,
}


# US-layout shifted symbols (keycodes are layout-independent for letters/digits;
# avoid symbols beyond these in paths typed with mode="keys").
_SHIFTED = {"#": "3", "_": "-", ":": ";", "~": "`", "+": "="}


def _flags(mods: str) -> int:
    f = 0
    for m in filter(None, (mods or "").lower().replace("+", " ").split()):
        f |= _MODS[m]
    return f


_MOD_KEYS = [(Quartz.kCGEventFlagMaskControl, 59), (Quartz.kCGEventFlagMaskAlternate, 58),
             (Quartz.kCGEventFlagMaskShift, 56), (Quartz.kCGEventFlagMaskCommand, 55)]


def _keycode(name: str, flags: int = 0) -> None:
    """Press a key with real modifier key-down/up events around it. Out-of-
    process views (the native save panel) ignore flag-only modifiers."""
    code = KEYCODES[name.lower()]
    held, acc = [], 0
    for mask, mcode in _MOD_KEYS:
        if flags & mask:
            acc |= mask
            ev = Quartz.CGEventCreateKeyboardEvent(None, mcode, True)
            Quartz.CGEventSetFlags(ev, acc)
            _post(ev)
            held.append((mask, mcode))
            time.sleep(0.02)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        _post(ev)
        time.sleep(0.03)
    for mask, mcode in reversed(held):
        acc &= ~mask
        ev = Quartz.CGEventCreateKeyboardEvent(None, mcode, False)
        Quartz.CGEventSetFlags(ev, acc)
        _post(ev)
        time.sleep(0.02)


def key(combo: str, times: int = 1, allow_panel: bool = False) -> None:
    """'return', 'escape', 'cmd+s', 'cmd+shift+s', 'cmd+a' ..."""
    S.spend("key")
    parts = combo.lower().replace(" ", "").split("+")
    *mods, k = parts
    flags = 0
    for m in mods:
        flags |= _MODS[m]
    for _ in range(max(1, times)):
        guard(allow_panel)
        _keycode(k, flags)
        time.sleep(0.05)
    S.log("key", combo=combo, times=times)


def ascii_input_source() -> None:
    """Press 英数 so the Japanese IME does not swallow typed characters."""
    guard(allow_panel=True)
    _keycode("eisu")
    time.sleep(0.1)


def type_text(text: str, mode: str = "unicode", allow_panel: bool = False) -> None:
    """mode=unicode: CGEvent unicode keystrokes (IME-independent payload).
    mode=keys: physical keycodes for [a-z0-9 .-#] only (for numeric boxes).
    mode=paste: pasteboard + Cmd+V (restores the previous clipboard)."""
    S.spend("type")
    guard(allow_panel)
    if mode == "paste":
        pb = NSPasteboard.generalPasteboard()
        old = pb.stringForType_(NSPasteboardTypeString)
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        _keycode("v", _MODS["cmd"])
        time.sleep(0.25)
        pb.clearContents()
        if old is not None:
            pb.setString_forType_(old, NSPasteboardTypeString)
    elif mode == "keys":
        for ch in text:
            guard(allow_panel)
            if ch in _SHIFTED:
                _keycode(_SHIFTED[ch], _MODS["shift"])
            elif ch.isupper():
                _keycode(ch.lower(), _MODS["shift"])
            else:
                _keycode("space" if ch == " " else ch)
            time.sleep(0.02)
    else:
        for i in range(0, len(text), 1):
            guard(allow_panel)
            chunk = text[i:i + 1]
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
                _post(ev)
            time.sleep(0.025)
    S.log("type", text=text, mode=mode)


def clear_field(allow_panel: bool = False) -> None:
    """Select-all then delete in the focused text box."""
    key("cmd+a", allow_panel=allow_panel)
    time.sleep(0.05)
    key("delete", allow_panel=allow_panel)
