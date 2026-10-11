"""Working folder, action log and the step budget.

Everything the harness writes lives under one dedicated folder
(VROID_HARNESS_HOME, default ~/VRoidHarness):

    captures/   every screenshot, named <seq>-<tag>.png
    logs/       actions.jsonl - one line per action with before/after shots
    projects/   .vroid saves
    exports/    .vrm exports
    specs/      character specs (JSON)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HOME = Path(os.environ.get("VROID_HARNESS_HOME", Path.home() / "VRoidHarness")).expanduser()
CAPTURES = HOME / "captures"
LOGS = HOME / "logs"
PROJECTS = HOME / "projects"
EXPORTS = HOME / "exports"
SPECS = HOME / "specs"
for _d in (CAPTURES, LOGS, PROJECTS, EXPORTS, SPECS):
    _d.mkdir(parents=True, exist_ok=True)

# Hard ceiling on input actions per server process, so a confused agent loop
# cannot click forever. vroid_reset_budget() raises it deliberately.
MAX_STEPS = int(os.environ.get("VROID_HARNESS_MAX_STEPS", "600"))


class BudgetExceeded(RuntimeError):
    pass


class _State:
    seq = 0
    steps = 0
    budget = MAX_STEPS
    started = time.strftime("%Y%m%d-%H%M%S")


S = _State()
LOG_FILE = LOGS / f"actions-{S.started}.jsonl"


def next_seq() -> int:
    S.seq += 1
    return S.seq


def capture_path(tag: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (tag or "shot"))[:60]
    return CAPTURES / f"{S.started}-{next_seq():05d}-{safe}.png"


def spend(kind: str) -> None:
    """Count one input action against the budget (raises when exhausted)."""
    if S.steps >= S.budget:
        raise BudgetExceeded(
            f"step budget exhausted ({S.steps}/{S.budget}). Stop, re-observe, "
            "and call vroid_reset_budget only if the plan is sound.")
    S.steps += 1


def log(event: str, **fields) -> None:
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": S.steps, "event": event}
    rec.update({k: (str(v) if isinstance(v, Path) else v) for k, v in fields.items()})
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Never overwrite: foo.vrm -> foo-2.vrm -> foo-3.vrm ..."""
    p = directory / f"{stem}{suffix}"
    n = 2
    while p.exists():
        p = directory / f"{stem}-{n}{suffix}"
        n += 1
    return p
