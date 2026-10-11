"""Character spec (JSON) -> VRoid Studio operations.

Claude turns a natural-language brief into a spec like docs/spec-example.json.
Choices that need eyes (which hairstyle / outfit / shoe thumbnail matches the
brief) are made by Claude from vroid_scan_presets contact sheets and written
into the spec as preset indices; everything else is applied here
deterministically and verified step by step.

    {
      "name": "parkour-sailor",                 # file stem for .vroid / .vrm
      "base": "female" | "male",
      "hair":   {"preset": 21, "color": "#202020", "highlight": null},
      "eyes":   {"color": "#5A3A2A"},           # optional
      "outfit": {
        "full_set": 52,                          # 全身セット preset (optional)
        "items": [                               # per category, applied after full_set
          {"category": "靴", "preset": 56},
          {"category": "トップス", "color": "#202B50", "shade": null, "sections": [0]}
        ]
      },
      "body":   {"脚の長さ": 0.3, "頭の大きさ": -0.1},   # 体型 labels as in the UI
      "export": {"avatar_name": "...", "author": "...", "version": "1.0", "format": "1.0"}
    }

Anything the spec asks for that VRoid's standard presets cannot do must be
listed by Claude under "unsupported" with the reason; apply_spec reports it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import session as S
from . import vroid as V

OUTFIT_CATEGORIES = ["全身セット", "トップス", "ボトムス", "ワンピース", "首飾り", "腕飾り",
                     "上半身インナー", "下半身インナー", "レッグウェア", "靴"]


def validate(spec: dict) -> list[str]:
    errs = []
    if not spec.get("name"):
        errs.append("name is required (file stem)")
    if spec.get("base", "female") not in ("female", "male"):
        errs.append("base must be female|male")
    for path, val in (("hair.color", spec.get("hair", {}).get("color")),
                      ("hair.highlight", spec.get("hair", {}).get("highlight")),
                      ("eyes.color", spec.get("eyes", {}).get("color"))):
        if val:
            try:
                V.hex_norm(val)
            except ValueError as e:
                errs.append(f"{path}: {e}")
    for it in spec.get("outfit", {}).get("items", []):
        if it.get("category") not in OUTFIT_CATEGORIES:
            errs.append(f"outfit item category {it.get('category')!r} not one of {OUTFIT_CATEGORIES}")
        if it.get("color"):
            try:
                V.hex_norm(it["color"])
            except ValueError as e:
                errs.append(f"outfit {it.get('category')}: {e}")
    return errs


def apply_spec(spec: dict | str | Path, new: bool = True, export: bool = True,
               views: bool = True) -> dict:
    """Build one character from a spec. Saves after every stage (checkpoint),
    never overwrites existing files, returns a per-step report."""
    if not isinstance(spec, dict):
        spec = json.loads(Path(spec).read_text())
    errs = validate(spec)
    if errs:
        raise ValueError("invalid spec: " + "; ".join(errs))
    (S.SPECS / f"{V.safe_name(spec['name'])}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    rep: dict = {"name": spec["name"], "steps": [], "unsupported": spec.get("unsupported", [])}

    def step(label, fn, *a, **kw):
        t = time.time()
        try:
            r = fn(*a, **kw)
            rep["steps"].append({"step": label, "ok": True, "result": r, "sec": round(time.time() - t, 1)})
            return r
        except Exception as e:
            rep["steps"].append({"step": label, "ok": False, "error": str(e)})
            S.log("apply_spec_error", step=label, error=str(e))
            raise

    V.ensure_ready()
    if new:
        step("create_character", V.create_character, spec.get("base", "female"))
        rep["project"] = step("save_as", V.save_project_as, spec["name"])["path"]

    hair = spec.get("hair", {})
    if hair.get("preset") is not None:
        V.open_category("髪セット", tab="hair")
        step("hair_preset", V.select_preset, int(hair["preset"]))
    if hair.get("color"):
        V.open_category("髪セット", tab="hair")
        step("hair_color", V.set_hex_row, ["メインカラー", "Main Color"], hair["color"])
        if hair.get("highlight"):
            step("hair_highlight", V.change_hair_color, hair["color"], hair["highlight"])
    step("checkpoint_hair", V.save_project)

    if spec.get("eyes", {}).get("color"):
        step("eye_color", V.change_eye_color, spec["eyes"]["color"], spec["eyes"].get("right"))

    outfit = spec.get("outfit", {})
    if outfit.get("full_set") is not None:
        V.open_category("全身セット", tab="outfit")
        step("full_set", V.select_preset, int(outfit["full_set"]))
    for it in outfit.get("items", []):
        cat = it["category"]
        if it.get("preset") is not None:
            V.open_category(cat, tab="outfit")
            step(f"{cat}_preset", V.select_preset, int(it["preset"]))
        if it.get("color"):
            V.open_category(cat, tab="outfit")
            V.open_texture_editor()
            step(f"{cat}_color", V.set_shader_colors, it["color"], it.get("shade"),
                 it.get("sections"), it.get("match"))
            V.close_texture_editor()
    step("checkpoint_outfit", V.save_project)

    if spec.get("body"):
        V.open_tab("body")
        for k, v in spec["body"].items():
            r = step(f"body:{k}", V.set_parameter, k, float(v))
            if not r["ok"]:
                rep.setdefault("warnings", []).append(f"{k}: requested {v}, got {r['after']}")
        step("checkpoint_body", V.save_project)

    if views:
        rep["views"] = step("views", V.capture_views, V.safe_name(spec["name"]))
    if export:
        ex = spec.get("export", {})
        rep["vrm"] = step("export_vrm", V.export_vrm, spec["name"],
                          ex.get("avatar_name", spec["name"]), ex.get("author", "vroid-harness"),
                          ex.get("version", "1.0"), ex.get("format", "1.0"))
        V.close_subeditor()
    rep["ok"] = all(s["ok"] for s in rep["steps"])
    S.log("apply_spec", name=spec["name"], ok=rep["ok"])
    return rep


def apply_specs(specs: list[dict], **kw) -> list[dict]:
    """Several characters in sequence. Stops at the first failure (the GUI
    state after a failure must be re-observed by Claude, not guessed)."""
    out = []
    for sp in specs:
        r = apply_spec(sp, **kw)
        out.append(r)
        if not r.get("ok"):
            break
    return out
