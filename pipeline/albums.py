"""Album rules and the editable content state files (no numpy/cv2 needed).

Imported by build.py, share.py, deploy-side tooling and the admin service so the
time rule, the overrides and the private list have exactly one implementation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PIPELINE = Path(os.environ.get("RP_PIPELINE_DIR", Path(__file__).resolve().parent))
EXCLUDE_FILE = PIPELINE / "exclude.txt"
NON_SBS_FILE = PIPELINE / "non_sbs.txt"
PRIVATE_FILE = PIPELINE / "private_albums.txt"
OVERRIDES_FILE = PIPELINE / "album_overrides.txt"

STAMP = re.compile(r"SV_(\d{8})_(\d{6})")


def album_for(pid: str) -> str:
    """Time-based album of a photo id (before any override)."""
    day, clock = pid.split("-")
    minutes = int(clock[:2]) * 60 + int(clock[2:4])
    if day == "20261101":
        return "oceanografic" if minutes < 15 * 60 + 30 else "ciutat"
    if day == "20261102":
        return "forum" if minutes < 19 * 60 else "abend"
    return "forum"


def photo_id(path: Path) -> str:
    match = STAMP.search(path.stem)
    if not match:
        raise ValueError(f"unexpected filename: {path.name}")
    day, clock = match.groups()
    return f"{day}-{clock}"


def _read_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text().splitlines():
        pid = line.split("#", 1)[0].strip()
        if pid:
            ids.add(pid)
    return ids


def read_exclude() -> set[str]:
    return _read_ids(EXCLUDE_FILE)


def read_non_sbs() -> set[str]:
    """Photos the build must not keep: ordinary photos, not SBS frames."""
    return _read_ids(NON_SBS_FILE)


def write_non_sbs(ids: set[str]) -> None:
    """Persist the non-SBS list so a rebuild does not reopen every flat JPEG."""
    tmp = NON_SBS_FILE.with_suffix(".txt.tmp")
    body = "# Photos without a side-by-side stereo pair: skipped by build.py and add_photo.py.\n"
    body += "".join(f"{pid}\n" for pid in sorted(ids))
    tmp.write_text(body)
    tmp.replace(NON_SBS_FILE)


def read_private() -> set[str]:
    """Albums that must never reach the public build (served as /s/ shares)."""
    if not PRIVATE_FILE.exists():
        return set()
    slugs = set()
    for line in PRIVATE_FILE.read_text().splitlines():
        slug = line.split("#", 1)[0].strip()
        if slug:
            slugs.add(slug)
    return slugs


def write_private(slugs: set[str]) -> None:
    """Rewrite the private list, keeping the file's own comments."""
    comments = []
    if PRIVATE_FILE.exists():
        for line in PRIVATE_FILE.read_text().splitlines():
            if line.strip().startswith("#") or not line.split("#", 1)[0].strip():
                comments.append(line)
    while comments and not comments[-1].strip():
        comments.pop()
    body = "\n".join(comments + [""] + sorted(slugs)).strip("\n")
    PRIVATE_FILE.write_text(body + "\n")


def read_overrides() -> dict[str, str]:
    """Explicit per-photo album assignments: "<photo-id> <album-slug>"."""
    if not OVERRIDES_FILE.exists():
        return {}
    rows = {}
    for line in OVERRIDES_FILE.read_text().splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2:
            rows[parts[0]] = parts[1]
    return rows
