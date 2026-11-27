"""Content operations for the admin UI: manifests, moves, shares.

All functions run as the repo owner (hermes). They never render; they only move
already-rendered photo directories and rewrite manifests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from albums import OVERRIDES_FILE, album_for, read_overrides, read_private, write_private
import publish
import share as shares_mod

REPO = Path(os.environ.get("RP_REPO", Path(__file__).resolve().parent.parent))
ALBUMS_JSON = REPO / "web" / "albums.json"
PUBLIC = REPO / "build"
PRIVATE = REPO / "build-private"
SHARES_ROOT = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
SHARES = SHARES_ROOT / "shares"
SOURCE = REPO / "source" / "Valencia"

TREES = {"public": PUBLIC, "private": PRIVATE}
PHOTO_FILES = ("scene.jpg", "sbs.jpg", "depth.jpg", "thumb.jpg", "meta.json")


def load_manifest(tree: Path) -> list[dict]:
    path = tree / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else []


def write_manifest(tree: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda row: row["id"])
    (tree / "manifest.json").write_text(json.dumps(rows, indent=1) + "\n")


def rebuild_manifest(tree: Path, overrides: dict[str, str] | None = None) -> list[dict]:
    """Rescan a tree's photo dirs and rewrite its manifest (no rendering)."""
    overrides = read_overrides() if overrides is None else overrides
    rows = []
    photos = tree / "photos"
    if not photos.is_dir():
        write_manifest(tree, rows)
        return rows
    for out in sorted(photos.iterdir()):
        if not out.is_dir():
            continue
        meta_file = out / "meta.json"
        meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        rows.append({"id": out.name,
                     "album": overrides.get(out.name) or album_for(out.name),
                     "ok": meta.get("depth", True) is not False,
                     **meta})
    write_manifest(tree, rows)
    return rows


def catalog() -> dict:
    """Every album with its public and private photo rows and current share(s)."""
    public = load_manifest(PUBLIC)
    private = load_manifest(PRIVATE)
    scores = publish.load_scores()
    gates = publish.load_gates()
    reg = shares_mod.load_registry()["shares"]
    albums: dict[str, dict] = {}
    for visibility, rows in (("public", public), ("private", private)):
        for row in rows:
            entry = albums.setdefault(row.get("album", "forum"),
                                      {"slug": row.get("album", "forum"),
                                       "public": [], "private": [], "shares": []})
            entry[visibility].append({**row, "score": scores.get(row["id"])})
    for slug, entry in albums.items():
        entry["shares"] = [s for s in reg if entry["slug"] in (s.get("albums") or [])]
        entry["mixed"] = bool(entry["private"]) and bool(entry["public"])
        entry["is_private"] = bool(entry["private"]) and not entry["public"]
        entry["count"] = len(entry["public"]) + len(entry["private"])
        entry["gate"] = gates.get(slug, 0.0)
        entry["published"] = len([r for r in entry["public"]
                                  if publish.allowed(r, scores, gates)])
    try:
        labels = json.loads(ALBUMS_JSON.read_text())
    except OSError:
        labels = {}
    for slug, meta in labels.items():
        entry = albums.setdefault(slug, {"slug": slug, "public": [], "private": [],
                                         "shares": [], "count": 0, "is_private": slug in read_private(),
                                         "gate": gates.get(slug, 0.0), "published": 0})
        entry["label"] = meta.get("label", slug)
        entry["sub"] = meta.get("sub", "")
        entry["url"] = meta.get("url", "")
    for entry in albums.values():
        entry.setdefault("label", entry["slug"])
    return dict(sorted(albums.items(), key=lambda kv: kv[1]["label"].lower()))


def slugify(label: str) -> str:
    umlauts = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    text = label.lower()
    for source, target in umlauts.items():
        text = text.replace(source, target)
    slug = "".join(ch if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in slug.split("-") if part)[:60] or "album"


def create_album(label: str, sub: str = "", url: str = "", private: bool = False) -> dict:
    """Add an album to web/albums.json (and the private list); photos come later."""
    labels = json.loads(ALBUMS_JSON.read_text()) if ALBUMS_JSON.exists() else {}
    slug = slugify(label)
    base, counter = slug, 2
    while slug in labels:
        slug = f"{base}-{counter}"
        counter += 1
    meta = {"label": label, "sub": sub}
    if url:
        meta["url"] = url
    labels[slug] = meta
    ALBUMS_JSON.write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n")
    if private:
        private_slugs = read_private()
        private_slugs.add(slug)
        write_private(private_slugs)
    return {"slug": slug, "private": private}


def rename_album(slug: str, label: str, sub: str = "", url: str = "") -> dict:
    """Change an album's display label, subtitle and link; slug and photos stay put."""
    labels = json.loads(ALBUMS_JSON.read_text())
    if slug not in labels:
        raise ValueError(f"unbekanntes Album: {slug}")
    previous = labels[slug]
    labels[slug] = {"label": label, "sub": sub, **({"url": url} if url else {})}
    ALBUMS_JSON.write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n")
    return {"slug": slug, "old": previous, "new": labels[slug]}


def deploy() -> tuple[bool, str]:
    if os.environ.get("RP_DEPLOY", "1") == "0":
        return True, "skipped (RP_DEPLOY=0)"
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "deploy.py")],
                            capture_output=True, text=True, timeout=120)
    output = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, (output[-1] if output else "")


def _drop_override(pid: str) -> None:
    """Remove the photo's album pin, keeping the file's comments intact."""
    if not OVERRIDES_FILE.exists():
        return
    keep = []
    for line in OVERRIDES_FILE.read_text().splitlines():
        parts = line.split("#", 1)[0].split()
        if parts[:1] == [pid]:
            continue
        keep.append(line)
    OVERRIDES_FILE.write_text("\n".join(keep).rstrip("\n") + "\n")


def _move_cover(src: Path, dst: Path, slug: str) -> None:
    cover = src / "covers" / f"{slug}.jpg"
    if cover.exists():
        (dst / "covers").mkdir(parents=True, exist_ok=True)
        shutil.move(str(cover), str(dst / "covers" / cover.name))


def set_visibility(slug: str, make_private: bool) -> dict:
    """Move every photo of an album between the public and private trees."""
    private_slugs = read_private()
    private_rows = [row for row in load_manifest(PRIVATE) if row.get("album") == slug]
    public_rows = [row for row in load_manifest(PUBLIC) if row.get("album") == slug]

    if make_private:
        if not public_rows and slug in private_slugs:
            return {"changed": False, "moved": 0, "slug": slug}
        src, dst, rows = PUBLIC, PRIVATE, public_rows
    else:
        if not private_rows and slug not in private_slugs:
            return {"changed": False, "moved": 0, "slug": slug}
        src, dst, rows = PRIVATE, PUBLIC, private_rows

    moved = 0
    (dst / "photos").mkdir(parents=True, exist_ok=True)
    for row in rows:
        source_dir = src / "photos" / row["id"]
        target_dir = dst / "photos" / row["id"]
        if source_dir.exists() and not target_dir.exists():
            os.rename(source_dir, target_dir)
            moved += 1
    _move_cover(src, dst, slug)

    if make_private:
        private_slugs.add(slug)
    else:
        private_slugs.discard(slug)
    write_private(private_slugs)

    rebuild_manifest(src)
    rebuild_manifest(dst)
    return {"changed": True, "moved": moved, "slug": slug, "deploy": deploy()}


def set_photo_visibility(pid: str, make_private: bool) -> dict:
    """Move ONE photo between the public and private tree (mixed albums)."""
    current = next((name for name, tree in TREES.items()
                    if (tree / "photos" / pid).is_dir()), None)
    if current is None:
        raise ValueError(f"photo not found: {pid}")
    target = "private" if make_private else "public"
    if current == target:
        return {"changed": False, "id": pid, "tree": current}
    src, dst = TREES[current], TREES[target]
    target_dir = dst / "photos" / pid
    if target_dir.exists():
        raise ValueError(f"{pid} already exists in {target}")
    (dst / "photos").mkdir(parents=True, exist_ok=True)
    os.rename(src / "photos" / pid, target_dir)
    rebuild_manifest(src)
    rebuild_manifest(dst)
    return {"changed": True, "id": pid, "from": current, "to": target, "deploy": deploy()}


def purge_from_shares(pid: str) -> list[str]:
    """Remove one photo from every share that contains it; drop emptied shares."""
    affected = []
    for entry in list(shares_mod.load_registry()["shares"]):
        token = entry["token"]
        photo_dir = SHARES / token / "photos" / pid
        manifest_file = SHARES / token / "photos" / "manifest.json"
        if not photo_dir.exists():
            continue
        shutil.rmtree(photo_dir, ignore_errors=True)
        rows = [row for row in json.loads(manifest_file.read_text()) if row["id"] != pid]
        if rows:
            manifest_file.write_text(json.dumps(rows, indent=1) + "\n")
        else:
            shares_mod.revoke_share(token)
        affected.append(token)
    return affected


def remove_photo(pid: str, reason: str = "admin") -> dict:
    """Delete a photo for good: render dir, source original, override, score, shares."""
    tree_name = next((name for name, tree in TREES.items()
                      if (tree / "photos" / pid).is_dir()), None)
    if tree_name is None:
        raise ValueError(f"photo not found: {pid}")

    tree = TREES[tree_name]
    shutil.rmtree(tree / "photos" / pid)
    source = SOURCE / f"SV_{pid[:8]}_{pid[9:]}.jpg"
    source_deleted = source.exists()
    source.unlink(missing_ok=True)

    _drop_override(pid)
    rebuild_manifest(tree)
    affected = purge_from_shares(pid)
    publish.drop_score(pid)

    result = {"id": pid, "tree": tree_name, "source_deleted": source_deleted,
              "shares": affected, "reason": reason}
    if tree_name == "public":
        result["deploy"] = deploy()
    return result
