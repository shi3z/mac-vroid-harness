"""VRoid Studio process + window discovery, activation and the focus guard.

Coordinates: CoreGraphics global space, in *points*, origin at the top-left of
the main display. Window bounds from CGWindowList and CGEvent positions both
live in this space, so no flipping is needed. Retina is handled in capture.py,
where image pixels are mapped back to points.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import Quartz
from AppKit import NSRunningApplication, NSWorkspace
import ApplicationServices as AS

BUNDLE_ID = "net.pixiv.vroid.macosx"
APP_PATH = "/Applications/VRoidStudio.app"
APP_NAME = "VRoid Studio"
# Sandboxed apps show NSSavePanel/NSOpenPanel from this out-of-process service.
PANEL_OWNERS = ("Open and Save Panel Service", "com.apple.appkit.xpc.openAndSavePanelService",
                "開くパネルと保存パネルのサービス")


@dataclass
class Window:
    wid: int
    pid: int
    owner: str
    title: str
    x: float
    y: float
    w: float
    h: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


def permissions() -> dict:
    return {
        "accessibility": bool(AS.AXIsProcessTrusted()),
        "screen_recording": bool(Quartz.CGPreflightScreenCaptureAccess()),
    }


def vroid_pid() -> int | None:
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID)
    for a in apps or []:
        if not a.isTerminated():
            return int(a.processIdentifier())
    return None


def _windows(onscreen_only: bool = True) -> list[Window]:
    opt = (Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
           if onscreen_only else Quartz.kCGWindowListOptionAll)
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opt, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        out.append(Window(int(w["kCGWindowNumber"]), int(w["kCGWindowOwnerPID"]),
                          str(w.get("kCGWindowOwnerName") or ""), str(w.get("kCGWindowName") or ""),
                          float(b.get("X", 0)), float(b.get("Y", 0)),
                          float(b.get("Width", 0)), float(b.get("Height", 0))))
    return out  # front-to-back order


def main_window() -> Window | None:
    """VRoid's editor window = its largest on-screen layer-0 window."""
    pid = vroid_pid()
    if pid is None:
        return None
    ws = [w for w in _windows() if w.pid == pid and w.w > 300 and w.h > 300]
    return max(ws, key=lambda w: w.w * w.h) if ws else None


def panel_window() -> Window | None:
    """A visible open/save panel (out-of-process for sandboxed VRoid)."""
    for w in _windows():
        if w.owner in PANEL_OWNERS and w.w > 200:
            return w
    return None


def frontmost_pid() -> int | None:
    """PID of the app that will receive keyboard input.

    Uses the Accessibility system-wide focused application, which is live even
    in a process with no run loop (NSWorkspace.frontmostApplication can be
    stale here). Falls back to the front-most normal window's owner.
    """
    try:
        sysw = AS.AXUIElementCreateSystemWide()
        err, app = AS.AXUIElementCopyAttributeValue(sysw, "AXFocusedApplication", None)
        if err == 0 and app is not None:
            err, pid = AS.AXUIElementGetPid(app, None)
            if err == 0:
                return int(pid)
    except Exception:
        pass
    ws = _windows()
    return ws[0].pid if ws else None


def front_owner() -> str:
    pid = frontmost_pid()
    if pid is None:
        return "?"
    a = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return str(a.localizedName()) if a else f"pid {pid}"


def is_vroid_front(allow_panel: bool = False) -> bool:
    vp = vroid_pid()
    if vp is None:
        return False
    fp = frontmost_pid()
    if fp == vp:
        return True
    if allow_panel and panel_window() is not None:
        ws = _windows()
        return bool(ws) and ws[0].owner in PANEL_OWNERS
    return False


def launch(timeout: float = 120.0) -> Window:
    if vroid_pid() is None:
        subprocess.run(["open", "-b", BUNDLE_ID], check=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        w = main_window()
        if w is not None:
            return w
        time.sleep(1.0)
    raise TimeoutError("VRoid Studio window did not appear")


def activate(timeout: float = 5.0) -> bool:
    """Bring VRoid to the front; True when it actually is."""
    if is_vroid_front():
        return True
    for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID) or []:
        a.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
    deadline = time.time() + timeout
    tried_open = False
    while time.time() < deadline:
        if is_vroid_front():
            time.sleep(0.3)
            return True
        if not tried_open and time.time() > deadline - timeout / 2:
            # macOS 14+ may refuse activation from a background process; `open`
            # goes through LaunchServices and is honoured.
            subprocess.run(["open", "-b", BUNDLE_ID])
            tried_open = True
        time.sleep(0.2)
    return is_vroid_front()


def displays() -> list[dict]:
    out = []
    err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
    for did in ids[:n]:
        b = Quartz.CGDisplayBounds(did)
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        px_w = Quartz.CGDisplayModeGetPixelWidth(mode) if mode else 0
        out.append({"id": int(did), "x": b.origin.x, "y": b.origin.y,
                    "w": b.size.width, "h": b.size.height,
                    "scale": (px_w / b.size.width) if b.size.width else 1.0,
                    "main": bool(Quartz.CGDisplayIsMain(did))})
    return out


# --- window placement (Accessibility API) ---------------------------------------

def _ax_main_window(pid: int):
    app = AS.AXUIElementCreateApplication(pid)
    err, wins = AS.AXUIElementCopyAttributeValue(app, "AXWindows", None)
    if err != 0 or not wins:
        raise RuntimeError(f"AX could not list VRoid windows (err {err}); "
                           "is Accessibility permission granted?")
    best, area = None, -1
    for w in wins:
        e1, size = AS.AXUIElementCopyAttributeValue(w, "AXSize", None)
        if e1 != 0:
            continue
        ok, sz = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        a = sz.width * sz.height if ok else 0
        if a > area:
            best, area = w, a
    return best


def visible_frames() -> list[dict]:
    """Each screen's usable area (minus menu bar / Dock) in CG top-left points."""
    from AppKit import NSScreen
    screens = NSScreen.screens()
    main_h = screens[0].frame().size.height
    out = []
    for s in screens:
        f, v = s.frame(), s.visibleFrame()
        out.append({"x": v.origin.x, "y": main_h - (v.origin.y + v.size.height),
                    "w": v.size.width, "h": v.size.height,
                    "frame": (f.origin.x, main_h - (f.origin.y + f.size.height),
                              f.size.width, f.size.height),
                    "scale": float(s.backingScaleFactor())})
    return out


def fit_window(screen: int | None = None, width: float | None = None,
               height: float | None = None) -> Window:
    """Move/resize VRoid's window fully inside one screen's visible area.

    screen=None picks the screen holding most of the window now. Every pixel of
    the UI must be on a display, or clicks on the off-screen part go nowhere.
    """
    pid = vroid_pid()
    w = main_window()
    if pid is None or w is None:
        raise RuntimeError("VRoid Studio is not running")
    frames = visible_frames()
    if screen is None:
        def overlap(f):
            ox = max(0, min(w.x + w.w, f["x"] + f["w"]) - max(w.x, f["x"]))
            oy = max(0, min(w.y + w.h, f["y"] + f["h"]) - max(w.y, f["y"]))
            return ox * oy
        f = max(frames, key=overlap)
    else:
        f = frames[screen]
    tw = min(width or f["w"], f["w"])
    th = min(height or f["h"], f["h"])
    axw = _ax_main_window(pid)
    pos = AS.AXValueCreate(AS.kAXValueCGPointType, Quartz.CGPointMake(f["x"], f["y"]))
    size = AS.AXValueCreate(AS.kAXValueCGSizeType, Quartz.CGSizeMake(tw, th))
    AS.AXUIElementSetAttributeValue(axw, "AXPosition", pos)
    AS.AXUIElementSetAttributeValue(axw, "AXSize", size)
    time.sleep(1.0)
    AS.AXUIElementSetAttributeValue(axw, "AXPosition", pos)  # re-apply after resize clamp
    time.sleep(0.8)
    return main_window()


def window_on_screen(w: Window | None = None) -> bool:
    """True when every corner of the window is on some display."""
    w = w or main_window()
    if w is None:
        return False
    ds = displays()

    def on(px, py):
        return any(d["x"] <= px < d["x"] + d["w"] and d["y"] <= py < d["y"] + d["h"] for d in ds)
    return all(on(px, py) for px, py in ((w.x + 1, w.y + 1), (w.x + w.w - 2, w.y + 1),
                                         (w.x + 1, w.y + w.h - 2), (w.x + w.w - 2, w.y + w.h - 2)))
