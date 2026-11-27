"""Publishing gates: an album can publish only photos at or above a score.

`pipeline/publish.json` maps album slug -> minimum composite score (0 = all).
Photos without a score (fresh uploads before scoring) always pass, so nothing
disappears by accident.

Imported by deploy.py, share.py and the admin UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PIPELINE = Path(os.environ.get("RP_PIPELINE_DIR", Path(__file__).resolve().parent))
STATE = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
GATES_FILE = PIPELINE / "publish.json"
SCORES_FILE = STATE / "scores.json"


def load_scores() -> dict:
    if not SCORES_FILE.exists():
        return {}
    try:
        return json.loads(SCORES_FILE.read_text()).get("scores", {})
    except json.JSONDecodeError:
        return {}


def load_gates() -> dict[str, float]:
    if not GATES_FILE.exists():
        return {}
    try:
        return {slug: float(value) for slug, value in json.loads(GATES_FILE.read_text()).items()}
    except (json.JSONDecodeError, ValueError):
        return {}


def save_gates(gates: dict[str, float]) -> None:
    clean = {slug: round(float(value), 2) for slug, value in gates.items() if float(value) > 0}
    GATES_FILE.write_text(json.dumps(clean, indent=1, sort_keys=True) + "\n")


def drop_score(pid: str) -> None:
    """Remove one photo's scores and clear pointers to it after a delete."""
    if not SCORES_FILE.exists():
        return
    data = json.loads(SCORES_FILE.read_text())
    scores = data.get("scores", {})
    changed = scores.pop(pid, None) is not None
    for row in scores.values():
        if row.get("nearest") == pid:
            row["nearest"] = None
            row["similarity"] = None
            changed = True
    if changed:
        tmp = SCORES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        tmp.replace(SCORES_FILE)


def allowed(row: dict, scores: dict | None = None, gates: dict | None = None) -> bool:
    scores = load_scores() if scores is None else scores
    gates = load_gates() if gates is None else gates
    gate = gates.get(row.get("album", ""), 0.0)
    if gate <= 0:
        return True
    score = scores.get(row.get("id", ""))
    if not score:
        return True
    return score.get("composite", 5.0) >= gate


def filter_rows(rows: list[dict], scores: dict | None = None,
                gates: dict | None = None) -> list[dict]:
    scores = load_scores() if scores is None else scores
    gates = load_gates() if gates is None else gates
    return [row for row in rows if allowed(row, scores, gates)]
