"""Pure cover selection shared by the builder and the admin UI."""
from __future__ import annotations

CURATED_COVERS = {
    "oceanografic": "20261101-121758",
    "ciutat": "20261101-164119",
    "forum": "20261102-153826",
    "abend": "20261102-223636",
}


def eligible(pid: str, scores: dict, gate: float) -> bool:
    """Same gate rule as publish.allowed: no score passes; below gate does not."""
    score = scores.get(pid) or {}
    composite = score.get("composite")
    if composite is None:
        return True
    try:
        return float(composite) >= float(gate or 0)
    except (TypeError, ValueError):
        return True


def choose_cover(ids: list[str], pin: str | None, curated: str | None,
                 scores: dict, gate: float) -> str | None:
    """Pin first, then curated, then lowest id; all candidates must pass the gate."""
    candidates = [pid for pid in ids if eligible(pid, scores, gate)]
    if not candidates:
        return None
    if pin in candidates:
        return pin
    if curated in candidates:
        return curated
    return sorted(candidates)[0]
