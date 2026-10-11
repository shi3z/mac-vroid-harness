# vroid-harness

MCP server (stdio) that gives Claude Code **eyes and hands for VRoid Studio on
macOS**. It drives the real GUI — screenshots go to Claude, CGEvent mouse and
keyboard go to VRoid — so characters are built in VRoid Studio itself, not by
editing VRM files.

Verified on macOS 26.5 (Apple Silicon), VRoid Studio 2.14.0 (Japanese UI),
Retina 2x, built-in display + external display.

## Reference repositories (investigated first)

| | what it is | licence | used how |
|---|---|---|---|
| [nhodges/mcp-vroid](https://github.com/nhodges/mcp-vroid) | Python MCP for VRoid on **Linux/Hyprland/Wine** (grim, tesseract, wlr virtual pointer, XTEST) | MIT | Design ported: see → locate → act → see again, numeric boxes over slider drags, focus guard, colour-blob buttons, flow functions. All platform code replaced. |
| [d-wwei/macos-desktop-control](https://github.com/d-wwei/macos-desktop-control) | Generic macOS desktop MCP (cliclick + AppleScript, JXA CGEvent background mode) | MIT | **Not reused at runtime.** Needs `cliclick` (not installed), spawns osascript/JXA per action (slow), its PID-targeted background events are commonly ignored by Unity apps, and it has no window-image-px ↔ point mapping. Only its CGEvent approach informed `input.py`. |

## Requirements

* Terminal (the app running Claude Code) needs **Screen Recording** and
  **Accessibility** in System Settings › Privacy & Security.
* `uv` (deps: mcp<2, pyobjc Quartz/Vision/ApplicationServices/Cocoa, pillow, numpy, opencv-headless).
* Install and register it with Claude Code (`.mcp.json` in your project, or `claude mcp add`):

```sh
git clone https://github.com/shi3z/mac-vroid-harness.git
cd mac-vroid-harness && uv sync
```

```json
{"mcpServers": {"vroid-harness": {"command": "uv",
  "args": ["run", "--directory", "/path/to/mac-vroid-harness", "vroid-harness"]}}}
```

`uv run python scripts/smoke_test.py --shot` starts it over stdio like Claude Code does.

## Work folder, safety

Everything goes under `~/VRoidHarness` (`VROID_HARNESS_HOME`):
`captures/` (every screenshot), `logs/actions-*.jsonl` (every input with the
shot it was aimed at), `projects/*.vroid`, `exports/*.vrm`, `specs/*.json`.

* **Focus guard**: every input re-checks that VRoid is the frontmost app (AX
  focused application) and that the point is inside its window; otherwise
  nothing is sent (`REFUSED`). Native save/open panels are allowed only inside
  the save/open flows.
* **No overwrites**: save-as/export pick `name-2`, `name-3`… and cancel if the
  panel asks to replace. Leaving a model with unsaved changes is refused.
* **Checkpoints**: `apply_spec` saves after hair, outfit and body stages.
* **Step budget**: 600 input actions per server process (`VROID_HARNESS_MAX_STEPS`);
  `vroid_reset_budget` raises it deliberately.
* **Verification**: values are read back by OCR, colours by OCR of the hex box,
  presets by viewport change, exports by file size + glTF magic.
* Errors come back with a recovery hint: re-observe before retrying.

## Tools (42) and the prompt

Stage 1 — eyes & hands: `vroid_status`, `vroid_launch`, `vroid_fit_window`,
`vroid_activate`, `vroid_screenshot`, `vroid_zoom`, `vroid_find_text`,
`vroid_read_screen`, `vroid_click`, `vroid_drag`, `vroid_scroll`, `vroid_type`,
`vroid_key`, `vroid_reset_budget`. Coordinates default to **image px** of the
full-resolution capture; `space="shown"` accepts coordinates read off the
downscaled image, `space="points"` raw CG points.

Stage 2 — VRoid operations: `vroid_create_character`, `vroid_open_character`,
`vroid_current_screen`, `vroid_open_editor_tab`, `vroid_list_categories`,
`vroid_open_category`, `vroid_list_parameters`, `vroid_find_parameter`,
`vroid_set_parameter`, `vroid_set_hex_row`, `vroid_scan_presets`,
`vroid_select_preset`, `vroid_select_hair_style`, `vroid_change_hair_color`,
`vroid_change_eye_color`, `vroid_edit_body_proportions`, `vroid_select_outfit`,
`vroid_open_texture_editor`, `vroid_list_material_colors`,
`vroid_change_outfit_color`, `vroid_close_texture_editor`,
`vroid_rotate_camera`, `vroid_capture_character_views`, `vroid_save_project`,
`vroid_export_vrm`.

Stages 3–5: `vroid_validate_spec`, `vroid_apply_spec`, `vroid_apply_specs`,
and the MCP prompt **`build_character(brief)`** (observe → choose presets by
eye → apply → four-view check → fix → save → export).

## How the real UI maps to operations (VRoid 2.14.0, ja)

Discovered by the harness itself (`vroid_list_categories`, `vroid_list_parameters`) —
nothing is guessed:

* Tabs: 顔 / 髪型 / 体型 / 衣装 / アクセサリー / ルック.
* 顔 rail: 顔セット, 目セット, 瞳, 瞳のハイライト, 白目, まゆげ, まぶた, アイライン,
  まつげ, 鼻, 口, 口内, 口紅, チーク, 肌, フェイスペイント, 表情編集.
* 髪型 rail: 髪セット (38 presets + global メインカラー), 前髪, 後髪, 一体型,
  つけ髪, 横髪, アホ毛, はね毛, ベースヘアー (each: メインカラー/ハイライトカラー hex).
* 体型: one category 全身, ~25 numeric rows (身長の高さ（女性）, 頭の大きさ, 脚の長さ…).
* 衣装 rail: 全身セット (70), トップス (62), ボトムス, ワンピース, 首飾り, 腕飾り,
  上半身インナー, 下半身インナー, レッグウェア, 靴 (60). Item colours are **not**
  hex rows: テクスチャを編集 → each material's シェーダーカラー 基本色/かげ色
  (multiplies the texture).
* 瞳 colour: テクスチャを編集 → カラー調整 → カラー調整を使う + カラー（左目/右目）.
* アクセサリー has no rail; ルック is a text list (アウトライン, リムライト, 陰影,
  目の可動域, 揺れもの).
* Menu ☰: 保存, 名前を付けて保存, カスタムアイテムをインポート, XWear一括エクスポート,
  元に戻す, やり直し, モデル選択に戻る. Share icon → VRMエクスポート → エクスポート →
  VRMエクスポート設定 modal (VRM1.0/0.0, アバター名※, バージョン, 作者※, licence
  defaults kept) → native save panel.

## Measured macOS/Unity quirks (each one cost a failure first)

* VRoid's window sat 128 pt off the left screen edge → clicks there vanish.
  `fit_window` moves it fully onto a display via AX.
* A human touching the mouse moves the cursor between our move and press →
  `CGAssociateMouseAndMouseCursorPosition(false)` for the few ms of each click.
* Unity drops a click whose press is too short (now 90 ms) and drops
  wheel/hover when the pointer did not *move* (always approach from 6 pt away).
* Nested layer lists in the texture editor swallow the wheel → scroll over the
  panel's scrollbar strip. Long wheel bursts are partially dropped → batches
  until the image stops changing; read lists only after the smooth scroll settles.
* Unity input field: one click selects all; **Cmd+A is eaten by the app menu**.
  Clear with End + Backspace.
* Native NSSavePanel (sandboxed app → remote view): ignores unicode-payload key
  events and Cmd+A; accepts physical keycodes and Ctrl+A/Ctrl+K. Folder via
  Cmd+Shift+G. File names are restricted to `[A-Za-z0-9.-]`.
* Colour popups: Escape closes them only sometimes, and stray keys on the
  texture canvas are tool shortcuts → only type after the popup's own hex box
  is found by region OCR; close by clicking the title text.
* Vision OCR: the Fast level does not support Japanese and **aborts the
  process**; full-window OCR misses small text (crop + upscale); `#B1CDD3` is
  read as `#BICDD3` (I/1, O/0 fixed up).
* Camera: right-drag orbits at 2.5 pt/deg, exact only if every motion event
  moves ≥ 5 pt; wheel down = zoom **out**. Front is found by left/right
  silhouette symmetry (the widest silhouette is a 3/4 view), front vs back by
  face skin. 体型 draws a height gauge, so views are taken from 衣装.
* Saving: the `*` leaves the title a few seconds after the write — poll.
* With ja OCR on, `#` is sometimes read as full-width `＃` (NFKC-normalise),
  and a kana IME turns keycodes into ひらがな → press 英数 before every typed value.
* White clothes on the light-grey viewport split the silhouette into blobs →
  the model box is the union of all large blobs.
* Texture painting: テクスチャを編集 → バケツ tool (塗りつぶしカラー hex, 許容値).
  Tolerance 30 leaks from the tie into the collar; 12 stays inside. Check each
  fill on the UV canvas and undo (↶ top-left) if it leaks.

## Example run (the brief in the task)

「黒髪ショートカットで、運動神経が良さそうな、紺色のセーラー服を着た女子高生…スニーカーは白と赤」
→ `docs/spec-example.json` → 髪セット #21 + `#202020`, 全身セット #52 (sailor),
トップス シェーダー `#202B50`, 靴 #56 (white/red sneakers), 脚の長さ 0.3,
頭の大きさ −0.1 → `~/VRoidHarness/projects/parkour-sailor.vroid`,
`~/VRoidHarness/exports/parkour-sailor.vrm` (VRM1.0, name パルクール少女).

Reproducibility: `apply_spec(docs/spec-example.json)` rebuilt it from a new
model in 14 verified steps (~6.5 min) → `parkour-sailor-r2.vroid/.vrm`; the VRM
is byte-for-byte the same size as the hand-driven build (14,313,988 bytes).

Revision v2 (user feedback: white shirt, navy skirt, red ribbon, semi-long
hair): トップス shader back to `#FFFFFF` (white body + navy collar of the
texture), tie and scarf bucket-filled `#C8202C` on the UV canvas, 髪セット #17
(semi-long) + `#202020` → `parkour-sailor-v2.vroid/.vrm` (15,395,736 bytes).

Reported limitation (v1): VRoid's sailor top is a single material, so "navy" via
シェーダーカラー darkens the collar lines too; white lines on a navy body would
need texture painting.

## Layout

```
src/vroid_harness/
  macos.py    process/window discovery, activation, focus, AX window fitting, displays
  capture.py  screencapture, Shot (image px <-> points, Retina scale), transport downscale
  input.py    CGEvent mouse/keyboard behind the focus guard, step budget
  locate.py   Apple Vision OCR (ja/en, per-character boxes), colour blobs, UI diff
  vroid.py    VRoid flows: tabs, rail, parameters, colours, texture editor,
              presets, camera/views, save/open/export, lifecycle
  spec.py     character spec -> operations (+ multiple characters)
  session.py  work folder, jsonl log, step budget, no-overwrite paths
  server.py   MCP tools (stdio)
```

MIT. VRoid Studio is a product of pixiv Inc.; unaffiliated.
