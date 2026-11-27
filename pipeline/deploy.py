#!/usr/bin/env python3
"""Deploy the public gallery to /var/www/randompics.de and verify it.

Never rsync `web/` with --delete against the docroot root: photos/ and covers/
are not part of web/ and would be wiped. They are excluded from the root sync
and deployed on their own.

Checks run against the live vhost through 127.0.0.1 (TLS + Host preserved).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOCROOT = Path("/var/www/randompics.de")
HOST = "randompics.de"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def rsync(src: Path, dst: Path, delete: bool = True, exclude: tuple[str, ...] = ()) -> None:
    cmd = ["rsync", "-a"] + (["--delete"] if delete else []) + [f"--exclude={e}" for e in exclude]
    cmd += [f"{src}/", f"{dst}/"]
    run(cmd)


def curl(path: str, head: bool = False) -> tuple[int, bytes]:
    cmd = ["curl", "-s", "--resolve", f"{HOST}:443:127.0.0.1",
           "-o", "-" if not head else "/dev/null"]
    if head:
        cmd.append("-w")
        cmd.append("%{http_code}")
    else:
        cmd.append("-w")
        cmd.append("\n%{http_code}")
    cmd.append(f"https://{HOST}{path}")
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    body, _, code = out.rpartition(b"\n")
    return int(code), body


def main() -> int:
    local = json.loads((REPO / "build" / "manifest.json").read_text())
    allow_empty = os.environ.get("RP_ALLOW_EMPTY") == "1"
    if not local and not allow_empty:
        print("build/manifest.json is empty — run build.py first", file=sys.stderr)
        return 1
    if not (REPO / "build" / "photos").is_dir():
        print("build/photos missing — run build.py first", file=sys.stderr)
        return 1

    selected = publish.filter_rows(local)

    rsync(REPO / "web", DOCROOT, exclude=("photos", "covers"))
    # Never publish labels of private albums: /albums.json is fetched by the gallery.
    labels = json.loads((REPO / "web" / "albums.json").read_text())
    public_albums = {row["album"] for row in local}
    published = {slug: meta for slug, meta in labels.items() if slug in public_albums}
    (DOCROOT / "albums.json").write_text(
        json.dumps(published, indent=2, ensure_ascii=False) + "\n")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        for row in selected:
            handle.write(f"{row['id']}/\n")
        files_from = handle.name
    try:
        subprocess.run(["rsync", "-a", "--delete", f"--files-from={files_from}",
                        f"{REPO / 'build' / 'photos'}/", f"{DOCROOT / 'photos'}/"],
                       check=True)
    finally:
        Path(files_from).unlink(missing_ok=True)
    # --files-from does not delete unlisted directories, so stale photos would stay
    # reachable by URL. Prune everything that is not in this deployment.
    selected_ids = {row["id"] for row in selected}
    for entry in (DOCROOT / "photos").iterdir():
        if entry.is_dir() and entry.name not in selected_ids:
            shutil.rmtree(entry)
        elif entry.is_file() and entry.name != "manifest.json":
            entry.unlink(missing_ok=True)
    (DOCROOT / "photos" / "manifest.json").write_text(json.dumps(selected, indent=1) + "\n")
    if os.environ.get("RP_SKIP_COVERS") == "1":
        # Fail closed: remove all public covers instead of republishing a stale pick.
        with tempfile.TemporaryDirectory() as empty:
            rsync(Path(empty), DOCROOT / "covers")
        (DOCROOT / "covers.json").write_text("{}\n")
    else:
        rsync(REPO / "build" / "covers", DOCROOT / "covers")
        cover_map = REPO / "build" / "covers.json"
        if cover_map.exists():
            (DOCROOT / "covers.json").write_text(cover_map.read_text())
        else:
            (DOCROOT / "covers.json").write_text("{}\n")
    run(["chmod", "-R", "a+rX", str(DOCROOT)])

    failures = []
    code, _ = curl("/", head=True)
    if code != 200:
        failures.append(f"index HTTP {code}")

    code, body = curl("/photos/manifest.json")
    served = json.loads(body) if code == 200 else []
    if len(served) != len(selected):
        failures.append(f"served manifest {len(served)} != selected {len(selected)}")

    first = selected[0]["id"] if selected else None
    if first:
        code, _ = curl(f"/photos/{first}/scene.jpg", head=True)
        if code != 200:
            failures.append(f"sample photo HTTP {code}")

    if os.environ.get("RP_SKIP_COVERS") != "1" and local:
        slug = local[0]["album"]
        code, _ = curl(f"/covers/{slug}.jpg", head=True)
        if code != 200:
            failures.append(f"cover HTTP {code}")

    code, body = curl("/robots.txt")
    if code != 200 or b"Disallow: /s/" not in body:
        failures.append("robots.txt missing or does not disallow /s/")

    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"OK: {len(selected)}/{len(local)} photos in {len({p['album'] for p in selected})} albums live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
