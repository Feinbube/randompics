#!/usr/bin/env python3
"""Add uploaded photos to an album, render them and score them.

    add_photo.py --album SLUG --visibility public|private FILES...

Runs with the sbs3d venv (cv2/torch-free render stack). Each file is copied to
the source folder as SV_<date>_<time>.jpg, rendered into the album's tree, pinned
to the album in album_overrides.txt, the manifest is rebuilt and the new photos
are scored. Public trees are redeployed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import albums as album_rules  # noqa: E402
import library  # noqa: E402
from render import NotStereo, render_fallback, render_photo  # noqa: E402

REPO = Path(os.environ.get("RP_REPO", Path(__file__).resolve().parent.parent))
SOURCE = REPO / "source" / "Valencia"


def next_id(base: datetime, taken: set[str]) -> str:
    stamp = base.timestamp()
    while True:
        pid = datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M%S")
        if pid not in taken:
            taken.add(pid)
            return pid
        stamp += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--album", required=True)
    ap.add_argument("--visibility", choices=("public", "private"), required=True)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    tree = library.PUBLIC if args.visibility == "public" else library.PRIVATE
    taken = {p.name for p in (tree / "photos").glob("*") if p.is_dir()}
    for other in (library.PUBLIC, library.PRIVATE):
        taken |= {p.name for p in (other / "photos").glob("*") if p.is_dir()}
    taken |= {p.stem[3:].replace("_", "-") for p in SOURCE.glob("SV_*.jpg")}

    SOURCE.mkdir(parents=True, exist_ok=True)
    (tree / "photos").mkdir(parents=True, exist_ok=True)
    created = []
    skipped = []
    for path_str in args.files:
        src = Path(path_str)
        if not src.is_file():
            print(f"skip missing {src}", file=sys.stderr)
            continue
        base = datetime.fromtimestamp(src.stat().st_mtime)
        mtime_id = base.strftime("%Y%m%d-%H%M%S")
        pid = mtime_id if mtime_id not in taken else next_id(base, taken)
        taken.add(pid)

        original = SOURCE / f"SV_{pid[:8]}_{pid[9:]}.jpg"
        original_existed = original.exists()
        shutil.copy2(src, original)
        out = tree / "photos" / pid
        try:
            meta = render_photo(original, out)
            state = "2.5D"
        except NotStereo as exc:  # only SBS pairs belong in the collection
            shutil.rmtree(out, ignore_errors=True)
            if not original_existed:
                original.unlink(missing_ok=True)
            skipped.append(src.name)
            print(f"skip {src.name}: kein SBS-Paar ({exc})", file=sys.stderr)
            continue
        except Exception as exc:  # stereo pair whose depth failed: keep as plain entry
            meta = render_fallback(original, out)
            state = f"plain ({exc})"
        created.append(pid)
        print(f"{pid} -> {args.album} [{args.visibility}] {state}")

        with album_rules.OVERRIDES_FILE.open("a") as handle:
            handle.write(f"{pid} {args.album}\n")

    if skipped:
        print(f"{len(created)} hinzugefügt, {len(skipped)} übersprungen (kein SBS-Paar)")

    payload = {"ids": created, "skipped": skipped,
               "album": args.album, "visibility": args.visibility,
               "score_ok": None, "deploy_ok": None, "deploy_message": ""}
    if not created:
        print(json.dumps(payload))
        print("nothing added", file=sys.stderr)
        return 1

    library.rebuild_manifest(tree)
    # Rescore the whole collection: sharpness/depth are percentile ranks, so a
    # single-photo pass would always read 0.
    score_run = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "score.py")],
        check=False)
    score_ok = score_run.returncode == 0
    deploy_ok, deploy_message = True, ""
    if args.visibility == "public":
        deploy_ok, deploy_message = library.deploy()
        print(f"deploy {'OK' if deploy_ok else 'FAIL'}: {deploy_message}")
    payload.update({"score_ok": score_ok, "deploy_ok": deploy_ok,
                    "deploy_message": deploy_message})
    print(json.dumps(payload))
    return 0 if score_ok and deploy_ok else 1


if __name__ == "__main__":
    sys.exit(main())
