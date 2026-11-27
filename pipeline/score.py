#!/usr/bin/env python3
"""Automatic quality scores for every rendered photo (no model, deterministic).

Scores 0-5 per photo: sharpness, exposure, depth quality, duplicate separation,
plus a weighted composite. Sharpness and depth are percentile-mapped inside the
current collection, so a dark or low-texture scene is ranked against its peers
instead of against an absolute threshold (a sharp aquarium frame scores low on
raw Laplacian variance; see art-vision-qc -> bulk photo triage).

Output: $RP_SHARES_ROOT/scores.json  (default /srv/randompics/scores.json)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402

STATE = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
OUT = STATE / "scores.json"

DUP_W, DUP_H = 320, 240


def photo_path(pid: str) -> tuple[Path, str] | None:
    for name, tree in library.TREES.items():
        candidate = tree / "photos" / pid
        if candidate.is_dir():
            return candidate, name
    return None


def technical(path: Path) -> dict:
    gray = cv2.imread(str(path / "scene.jpg"), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return {"sharp": 0.0, "clip": 1.0, "has_scene": False}
    small = cv2.resize(gray, (960, 720), interpolation=cv2.INTER_AREA)
    sharp = float(cv2.Laplacian(small, cv2.CV_64F).var())
    clip = float(np.mean((gray < 6) | (gray > 249)))
    return {"sharp": sharp, "clip": clip, "has_scene": True}


def depth_stats(path: Path) -> dict:
    meta_file = path / "meta.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    std = 0.0
    depth_file = path / "depth.jpg"
    if depth_file.exists():
        depth = cv2.imread(str(depth_file), cv2.IMREAD_GRAYSCALE)
        if depth is not None:
            std = float(np.std(cv2.resize(depth, (320, 240), interpolation=cv2.INTER_AREA)))
    return {"std": std, "ok": meta.get("depth", True) is not False,
            "source": meta.get("depth_source", "sgbm")}


def load_vector(path: Path) -> np.ndarray | None:
    gray = cv2.imread(str(path / "thumb.jpg"), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return None
    vec = cv2.resize(gray, (DUP_W, DUP_H), interpolation=cv2.INTER_AREA).astype(np.float32)
    vec = vec.reshape(-1)
    vec -= vec.mean()
    std = vec.std()
    return vec / std if std > 1e-6 else None


def percentile_scores(values: list[float], invert: bool = False) -> list[float]:
    """Map raw values to 0-5 by within-group rank (ties share a rank)."""
    if not values:
        return []
    if len(values) == 1:
        return [2.5]
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 / max(1, len(order) - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return [round((1 - r) * 5 if invert else r * 5, 2) for r in ranks]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", help="only these photo ids (default: all)")
    args = ap.parse_args()

    rows = library.load_manifest(library.PUBLIC) + library.load_manifest(library.PRIVATE)
    album_of = {row["id"]: row.get("album", "") for row in rows if row.get("id")}
    ids = [row["id"] for row in rows if row.get("id")]
    if args.ids:
        ids = [pid for pid in ids if pid in set(args.ids)]

    raw = {}
    for pid in ids:
        found = photo_path(pid)
        if not found:
            continue
        path, tree = found
        raw[pid] = {"tree": tree, **technical(path), **depth_stats(path)}

    present = [pid for pid in ids if pid in raw]
    # Rank within the photo's own album: a dark aquarium set must not be ranked
    # against sharp daylight photos, and a gate then means "weakest of this album".
    groups: dict[str, list[str]] = {}
    for pid in present:
        groups.setdefault(album_of.get(pid, ""), []).append(pid)
    sharp_scores, depth_scores = {}, {}
    for members in groups.values():
        for pid, value in zip(members, percentile_scores([raw[p]["sharp"] for p in members])):
            sharp_scores[pid] = value
        for pid, value in zip(members, percentile_scores([raw[p]["std"] for p in members])):
            depth_scores[pid] = value

    dup = {pid: (5.0, None, 0.0) for pid in present}
    vectors = {}
    for pid in present:
        vec = load_vector(photo_path(pid)[0])  # type: ignore[index]
        if vec is not None:
            vectors[pid] = vec
    if len(vectors) >= 2:
        names = list(vectors)
        matrix = np.stack([vectors[name] for name in names])
        sim = matrix @ matrix.T / (DUP_W * DUP_H)
        for i, name in enumerate(names):
            row = sim[i].copy()
            row[i] = -1
            nearest = int(row.argmax())
            value = float(row[nearest])
            if value >= 0.95:
                score = 1.0
            elif value >= 0.90:
                score = 3.0
            else:
                score = 5.0
            dup[name] = (score, names[nearest], value)

    scores = {}
    for pid in present:
        data = raw[pid]
        exposure = 5.0 * max(0.0, 1 - data["clip"] / 0.08)
        depth = depth_scores.get(pid, 0.0) if data["ok"] else 0.0
        dup_score, nearest, similarity = dup[pid]
        composite = (0.50 * sharp_scores.get(pid, 0.0) + 0.15 * exposure
                     + 0.20 * depth + 0.15 * dup_score)
        scores[pid] = {
            "sharpness": sharp_scores.get(pid, 0.0),
            "exposure": round(exposure, 2),
            "depth": depth,
            "duplicate": dup_score,
            "similarity": round(similarity, 3),
            "nearest": nearest,
            "composite": round(composite, 2),
            "sharp_raw": round(data["sharp"], 1),
            "clip_pct": round(data["clip"] * 100, 2),
            "depth_std": round(data["std"], 2),
            "depth_source": data["source"],
            "tree": data["tree"],
        }

    STATE.mkdir(parents=True, exist_ok=True)
    payload = {"computed": datetime.now().astimezone().isoformat(timespec="seconds"),
               "method": "laplacian+clipping+depth-std+ncc", "scores": scores}
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    os.chmod(OUT, 0o644)

    ordered = sorted(scores.items(), key=lambda kv: kv[1]["composite"], reverse=True)
    print(f"scored {len(scores)} photos -> {OUT}")
    for pid, row in ordered[:5]:
        print(f"  best {pid}  composite {row['composite']:.2f}  sharp {row['sharpness']:.1f} "
              f"depth {row['depth']:.1f} dup {row['duplicate']:.0f}")
    for pid, row in ordered[-3:]:
        print(f"  weak {pid}  composite {row['composite']:.2f}  sharp {row['sharpness']:.1f} "
              f"depth {row['depth']:.1f} dup {row['duplicate']:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
