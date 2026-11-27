#!/usr/bin/env python3
"""Batch-render the Valencia SBS photos into a static gallery build."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402
from albums import (album_for, photo_id, read_exclude, read_non_sbs, read_overrides,  # noqa: E402
                    read_private, write_non_sbs)
from render import COVERS, NotStereo, render_fallback, render_photo, write_covers  # noqa: E402

ALBUMS = ("oceanografic", "ciutat", "forum", "abend")


def prune_singles(shots: Path) -> set[str]:
    """Remove render dirs left by the old flat-photo fallback."""
    pruned = set()
    if not shots.is_dir():
        return pruned
    for out in shots.iterdir():
        if not out.is_dir():
            continue
        try:
            meta = json.loads((out / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        if meta.get("single") is True:
            shutil.rmtree(out, ignore_errors=True)
            pruned.add(out.name)
    return pruned


def public_photo_ids(build: Path) -> set[str]:
    """IDs already explicit in build/photos, so a private build never duplicates them."""
    if build.name != "build-private":
        return set()
    root = build.parent / "build" / "photos"
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir()}


def private_photo_ids(build: Path) -> set[str]:
    """IDs already explicit in build-private/photos, so a public build never re-renders them."""
    if build.name != "build":
        return set()
    root = build.parent / "build-private" / "photos"
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir()}


def reconcile_duplicates(build: Path) -> set[str]:
    """A photo must not exist in both trees; keep the opposite tree's copy."""
    if build.name == "build":
        other = build.parent / "build-private" / "photos"
    elif build.name == "build-private":
        other = build.parent / "build" / "photos"
    else:
        return set()
    if not other.is_dir():
        return set()
    here = build / "photos"
    removed = set()
    for entry in here.iterdir():
        if entry.is_dir() and (other / entry.name).is_dir():
            shutil.rmtree(entry)
            removed.add(entry.name)
    return removed


def covers_for(existing: list[dict]) -> dict[str, str]:
    """One cover per album with rows; curated COVERS wins when it is present."""
    by_album: dict[str, list[str]] = {}
    for row in existing:
        album = row.get("album")
        if album:
            by_album.setdefault(album, []).append(row["id"])
    covers = {}
    for slug, ids in by_album.items():
        curated = COVERS.get(slug)
        covers[slug] = curated if curated in ids else sorted(ids)[0]
    return covers


def jobs(source: Path, shots: Path, depth_dir: Path | None,
         excluded: set[str] | None = None,
         private: set[str] | None = None,
         only: set[str] | None = None,
         overrides: dict[str, str] | None = None,
         skip: set[str] | None = None) -> list[tuple[Path, Path, Path | None]]:
    overrides = overrides or {}
    files = sorted(source.glob("*.jpg"))
    rows = []
    for f in files:
        pid = photo_id(f)
        if excluded and pid in excluded:
            continue
        if skip and pid in skip:
            continue
        album = overrides.get(pid) or album_for(pid)
        if private and album in private:
            continue
        if only and album not in only:
            continue
        depth = None
        if depth_dir is not None:
            candidate = depth_dir / f"dav2_{pid}.png"
            if not candidate.exists():
                candidate = depth_dir / f"dav2_{pid.replace('-', '_')}.png"
            depth = candidate if candidate.exists() else None
        rows.append((f, shots / pid, depth))
    return rows


def run_one(job: tuple[Path, Path, Path | None]) -> dict:
    src, out, depth = job
    pid = out.name
    try:
        meta = render_photo(src, out, depth_source=depth)
        return {"id": pid, "ok": True, **meta}
    except NotStereo as exc:
        shutil.rmtree(out, ignore_errors=True)
        return {"id": pid, "ok": False, "non_sbs": True, "error": str(exc)}
    except Exception as exc:  # a stereo pair whose depth failed must not sink the batch
        meta = render_fallback(src, out)
        return {"id": pid, "ok": False, "error": str(exc), **meta}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="source/Valencia")
    ap.add_argument("--build", default="build")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--force", action="store_true", help="re-render photos that already have assets")
    ap.add_argument("--depth-dir", default=None,
                    help="folder of dav2_<id>.png depth maps; without it SGBM is computed")
    ap.add_argument("--album-slug", default=None,
                    help="force every photo into this album (private builds, e.g. vwa)")
    ap.add_argument("--only-albums", default=None,
                    help="comma-separated album slugs to build (private builds)")
    args = ap.parse_args()

    source = Path(args.source)
    build = Path(args.build)
    shots = build / "photos"
    shots.mkdir(parents=True, exist_ok=True)

    non_sbs = read_non_sbs()
    pruned = prune_singles(shots)
    non_sbs |= pruned
    for pid in sorted(pruned):          # flat-photo entries from the old fallback
        publish.drop_score(pid)
        print(f"non-sbs: {pid} (deleted)", flush=True)
    if pruned:
        write_non_sbs(non_sbs)

    excluded = read_exclude()
    for pid in sorted(excluded):          # excluded photos are deleted from the build
        src_dir = shots / pid
        if src_dir.exists():
            shutil.rmtree(src_dir)
            print(f"excluded: {pid} (deleted)", flush=True)

    duplicates = reconcile_duplicates(build)
    for pid in sorted(duplicates):
        print(f"duplicate in both trees: {pid} (kept the other tree)", flush=True)

    only = {s.strip() for s in args.only_albums.split(",") if s.strip()} if args.only_albums else set()
    private = read_private() if not (args.album_slug or only) else set()
    overrides = read_overrides()
    # Private albums are not rendered into a public build, but a mixed album is
    # allowed to keep the photos the owner explicitly moved to build/. The tree
    # itself is the per-photo visibility state; do not delete a public photo just
    # because its album is still on the album-level private list.
    public_ids = public_photo_ids(build)
    private_ids = private_photo_ids(build)
    todo = jobs(source, shots, Path(args.depth_dir) if args.depth_dir else None,
                excluded, private, only or None, overrides,
                skip=non_sbs | public_ids | private_ids)
    if not args.force:
        todo = [j for j in todo if not (j[1] / "scene.jpg").exists()]
    print(f"{len(todo)} photo(s) to render", flush=True)

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, j) for j in todo]
        for done, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            results.append(row)
            state = "ok" if row["ok"] else f"FAIL {row.get('error')}"
            print(f"[{done}/{len(futures)}] {row['id']} {state}", flush=True)

    detected = {row["id"] for row in results if row.get("non_sbs")}
    if detected:
        non_sbs |= detected
        write_non_sbs(non_sbs)
        for pid in sorted(detected):
            publish.drop_score(pid)
            print(f"non-sbs: {pid} (skipped)", flush=True)

    existing = []
    for out in sorted(shots.iterdir()):
        meta_file = out / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            existing.append({"id": out.name,
                             "album": args.album_slug or overrides.get(out.name) or album_for(out.name),
                             "ok": meta.get("depth", True) is not False, **meta})
        elif out.is_dir():
            existing.append({"id": out.name, "ok": False})
    existing.sort(key=lambda r: r["id"])

    (build / "manifest.json").write_text(json.dumps(existing, indent=1) + "\n")

    # One cover per album that actually has photos in this tree. A mixed album
    # (public photos inside a private album) still needs a cover for its public
    # card, otherwise app.js requests a 404.
    cover_ids = covers_for(existing)
    write_covers(shots, build / "covers", cover_ids)
    for cover in (build / "covers").glob("*.jpg"):
        if cover.stem not in cover_ids:
            cover.unlink(missing_ok=True)

    failed = [r for r in existing if not r["ok"]]
    print(f"manifest: {len(existing)} photos, {len(failed)} without depth", flush=True)
    for row in failed:
        print("  no depth:", row["id"], row.get("error", ""))


if __name__ == "__main__":
    main()
