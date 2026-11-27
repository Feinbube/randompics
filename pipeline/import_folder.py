#!/usr/bin/env python3
"""Import a folder of camera photos as private albums (LAN/local only).

    import_folder.py DIR [--prefix Kamera] [--gap-days 3] [--render] [--workers 12]

Groups photos by contiguous days (a gap larger than --gap-days starts a new
album), creates one private album per group, copies the originals into
source/Valencia as SV_<date>_<time>.jpg, pins each id to its album in
album_overrides.txt and optionally renders the new albums into build-private.

Nothing is sent anywhere: no vision, no network calls.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import albums as album_rules  # noqa: E402
import library  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "source" / "Valencia"
STAMP = re.compile(r"(20\d{6})[_-](\d{6})")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember")


def stamp_of(path: Path) -> datetime:
    match = STAMP.search(path.stem)
    if match:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    return datetime.fromtimestamp(path.stat().st_mtime)


def label_for(first: datetime, last: datetime) -> str:
    if first.date() == last.date():
        return f"Kamera {first:%d.%m.%Y}"
    return f"Kamera {first:%d.%m.%Y}–{last:%d.%m.%Y}"


def slug_for(first: datetime, last: datetime) -> str:
    if first.date() == last.date():
        return f"cam-{first:%Y%m%d}"
    return f"cam-{first:%Y%m%d}-{last:%Y%m%d}"


def clusters(items: list[tuple[datetime, Path]], gap_days: int):
    items = sorted(items)
    group = [items[0]]
    for item in items[1:]:
        if (item[0].date() - group[-1][0].date()).days > gap_days:
            yield group
            group = [item]
        else:
            group.append(item)
    yield group


def existing_ids() -> set[str]:
    ids = set()
    for tree in (library.PUBLIC, library.PRIVATE):
        for row in library.load_manifest(tree):
            ids.add(row["id"])
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--prefix", default="Kamera")
    ap.add_argument("--gap-days", type=int, default=3)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    folder = Path(args.folder)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if not files:
        print("no JPEGs found", file=sys.stderr)
        return 1
    items = [(stamp_of(path), path) for path in files]
    known = existing_ids()
    SOURCE.mkdir(parents=True, exist_ok=True)

    albums_created = []
    overrides = []
    skipped = 0
    for group in clusters(items, args.gap_days):
        first, last = group[0][0], group[-1][0]
        label = label_for(first, last).replace("Kamera", args.prefix, 1)
        labels = json.loads(library.ALBUMS_JSON.read_text()) if library.ALBUMS_JSON.exists() else {}
        existing = {meta.get("label"): slug for slug, meta in labels.items()}
        if label in existing:                      # re-run of a partial import
            slug = existing[label]
        else:
            created = library.create_album(label, sub=f"{len(group)} Fotos", private=True)
            slug = created["slug"]
        albums_created.append({"slug": slug, "label": label, "photos": len(group)})
        for moment, path in group:
            pid = f"{moment:%Y%m%d}-{moment:%H%M%S}"
            if pid in known:
                skipped += 1
                continue
            known.add(pid)
            target = SOURCE / f"SV_{moment:%Y%m%d}_{moment:%H%M%S}.jpg"
            if target.exists():
                skipped += 1
                continue
            shutil.copy2(path, target)
            overrides.append(f"{pid} {slug}")

    with album_rules.OVERRIDES_FILE.open("a") as handle:
        for line in overrides:
            handle.write(line + "\n")

    out = Path.home() / "artifacts" / f"import-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"albums": albums_created, "photos": len(overrides),
                               "skipped": skipped, "source": str(folder)}, indent=1))
    print(f"albums: {len(albums_created)}, photos: {len(overrides)}, skipped: {skipped}")
    for entry in albums_created:
        print(f"  {entry['slug']:28} {entry['photos']:4}  {entry['label']}")
    print(f"map: {out}")

    if args.render:
        slugs = ",".join(entry["slug"] for entry in albums_created)
        cmd = ["/home/hermes/projects/sbs3d/.venv/bin/python",
               str(Path(__file__).resolve().parent / "build.py"),
               "--source", "source/Valencia", "--build", "build-private",
               "--only-albums", slugs, "--workers", str(args.workers)]
        print("render:", " ".join(cmd), flush=True)
        result = subprocess.run(cmd, cwd=str(REPO))
        if result.returncode != 0:
            print("render failed", file=sys.stderr)
            return result.returncode
        print("scoring collection ...", flush=True)
        subprocess.run(["/home/hermes/projects/sbs3d/.venv/bin/python",
                        str(Path(__file__).resolve().parent / "score.py")], cwd=str(REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
