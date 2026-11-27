#!/usr/bin/env python3
"""Regenerate one or all public album covers from the current manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
from render import write_covers  # noqa: E402


def write_mapping(build_dir: Path, covers: dict[str, str], album: str | None = None) -> None:
    path = build_dir / "covers.json"
    mapping: dict[str, str] = {}
    if path.exists():
        try:
            mapping = json.loads(path.read_text())
        except (OSError, ValueError):
            mapping = {}
    if album:
        mapping[album] = covers[album]
    else:
        mapping = dict(covers)
    path.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--album")
    ap.add_argument("--id")
    ap.add_argument("--build", type=Path, default=Path("build"))
    args = ap.parse_args()
    if bool(args.album) != bool(args.id):
        print("--album and --id must be used together", file=sys.stderr)
        return 2

    if args.album:
        covers = {args.album: args.id}
    else:
        manifest = json.loads((args.build / "manifest.json").read_text())
        covers = build.covers_for(manifest)

    out = args.build / "covers"
    missing = [slug for slug, pid in covers.items()
               if not (args.build / "photos" / pid / "scene.jpg").is_file()]
    for slug in missing:
        (out / f"{slug}.jpg").unlink(missing_ok=True)
    if missing:
        print("missing cover source: " + ", ".join(sorted(missing)), file=sys.stderr)
        return 1

    try:
        write_covers(args.build / "photos", out, covers)
    except OSError as exc:
        print(f"cover write failed: {exc}", file=sys.stderr)
        return 1

    for cover in out.glob("*.jpg"):
        if cover.stem not in covers and not args.album:
            cover.unlink(missing_ok=True)
    unwritten = [slug for slug in covers if not (out / f"{slug}.jpg").is_file()]
    if unwritten:
        print("cover write failed: " + ", ".join(sorted(unwritten)), file=sys.stderr)
        return 1
    write_mapping(args.build, covers, args.album)
    print(f"covers: {len(covers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
