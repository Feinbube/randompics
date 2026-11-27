#!/usr/bin/env python3
"""Private album shares: unguessable, expiring links under /s/<token>/.

A share is a self-contained copy of the gallery (index/app/style + one album's
renders) in the shares tree, which Apache serves at https://randompics.de/s/<token>/.
The token is the credential: 128 bits from secrets.token_urlsafe(16). Expiry is
enforced by `prune`, which deletes the share directory and its registry entry.

Commands:
  create --album vwa --label "VWA Meeting" --from build-private [--days 14]
  list
  extend <token> [--days 14 | --expires YYYY-MM-DD]
  revoke <token>
  prune
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
ROOT = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
SHARES = ROOT / "shares"
REGISTRY = ROOT / "shares.json"
TZ = ZoneInfo("Europe/Berlin")
DEFAULT_DAYS = 30
PHOTO_FILES = ("scene.jpg", "sbs.jpg", "depth.jpg", "thumb.jpg", "meta.json")


def load_registry() -> dict:
    if not REGISTRY.exists():
        return {"shares": []}
    return json.loads(REGISTRY.read_text())


def save_registry(reg: dict) -> None:
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(REGISTRY)


def parse_expiry(expires: str | None, days: int | None) -> datetime:
    """Date-only means end of that day in Berlin; --days counts from today."""
    if expires:
        day = date.fromisoformat(expires)
        return datetime.combine(day, dtime(23, 59, 59), tzinfo=TZ)
    return datetime.now(TZ) + timedelta(days=DEFAULT_DAYS if days is None else days)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def is_expired(entry: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(entry["expires"]) <= now


def share_url(token: str) -> str:
    return f"https://randompics.de/s/{token}/"


def make_index(label: str, slug: str, sub: str, url: str = "") -> str:
    """web/index.html + window.RP_ALBUM injected before app.js."""
    html = (WEB / "index.html").read_text()
    config = json.dumps({"slug": slug, "label": label, "sub": sub, "url": url},
                        ensure_ascii=False).replace("</", "<\\/")
    script = f"<script>window.RP_ALBUM = {config};</script>\n"
    needle = '<script src="app.js'
    if needle not in html:
        raise SystemExit("index.html: app.js script tag not found")
    return html.replace(needle, script + needle, 1)


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:  # different filesystem
        shutil.copy2(src, dst)


def create_share(source_build: str | Path | list[str | Path], albums: list[str], label: str,
                 sub: str = "", url: str = "", days: int | None = None,
                 expires: str | None = None) -> dict:
    """Materialise one share and register it. Returns the registry entry.

    `source_build` may be a list of build dirs: an album's photos can live in
    build/ (public) and build-private/ (private) at the same time, and a share
    shows both — it is the album's complete set, not its public subset.
    """
    builds = source_build if isinstance(source_build, (list, tuple)) else [source_build]
    builds = [Path(build).resolve() for build in builds]

    counts = {slug: 0 for slug in albums}
    seen: set[str] = set()
    entries: list[tuple[dict, Path]] = []
    for build in builds:
        manifest_file = build / "manifest.json"
        if not manifest_file.exists():
            continue
        for photo in json.loads(manifest_file.read_text()):
            slug = photo.get("album")
            if slug not in counts or photo["id"] in seen:
                continue
            seen.add(photo["id"])
            counts[slug] += 1
            entries.append((photo, build))
    missing = [slug for slug, count in counts.items() if not count]
    if missing:
        raise ValueError(f"no photos for album(s) {missing} in "
                         f"{', '.join(str(build) for build in builds)}")
    photos = [photo for photo, _ in entries]
    # one album: open it directly; several: open "all" so the filter bar stays useful
    gallery_slug = albums[0] if len(albums) == 1 else "all"

    when = parse_expiry(expires, days)
    if when <= datetime.now(timezone.utc):
        raise ValueError("expiry is in the past")

    token = secrets.token_urlsafe(16)
    SHARES.mkdir(parents=True, exist_ok=True)
    dst = SHARES / token
    (dst / "photos").mkdir(parents=True, exist_ok=False)

    (dst / "index.html").write_text(make_index(label, gallery_slug, sub, url))
    for asset in ("viewer.js", "app.js", "style.css", "albums.json"):
        shutil.copy2(WEB / asset, dst / asset)
    # Album labels of private albums must not leak: keep public ones + this share's.
    try:
        public = {row.get("album") for row in json.loads((REPO / "build" / "manifest.json").read_text())}
        labels = json.loads((WEB / "albums.json").read_text())
        keep = {slug: meta for slug, meta in labels.items() if slug in public or slug in albums}
        (dst / "albums.json").write_text(json.dumps(keep, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        pass
    for slug in albums:
        for build in builds:
            cover = build / "covers" / f"{slug}.jpg"
            if cover.exists():
                link_or_copy(cover, dst / "covers" / cover.name)
                break

    for photo, build in entries:
        src_dir = build / "photos" / photo["id"]
        for name in PHOTO_FILES:
            if (src_dir / name).exists():
                link_or_copy(src_dir / name, dst / "photos" / photo["id"] / name)
    (dst / "photos" / "manifest.json").write_text(json.dumps(photos, indent=1) + "\n")

    for path in [dst, *dst.rglob("*")]:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)

    entry = {
        "token": token,
        "albums": albums,
        "label": label,
        "sub": sub,
        "url": url,
        "photos": len(photos),
        "created": iso(datetime.now(TZ)),
        "expires": iso(when),
    }
    reg = load_registry()
    reg["shares"].append(entry)
    save_registry(reg)
    return entry


def revoke_share(token: str) -> dict:
    """Delete a share directory and its registry entry. Returns the entry."""
    reg = load_registry()
    entry = find(reg, token)
    shutil.rmtree(SHARES / token, ignore_errors=True)
    reg["shares"].remove(entry)
    save_registry(reg)
    return entry


def cmd_create(args: argparse.Namespace) -> int:
    builds = [part.strip() for part in args.source_build.split(",") if part.strip()]
    entry = create_share(builds, args.albums, args.label, args.sub,
                         args.url, args.days, args.expires)
    print(f"created {entry['label']!r}: {entry['photos']} photos ({', '.join(entry['albums'])})")
    print(f"  {share_url(entry['token'])}")
    print(f"  expires {entry['expires']}  (revoke: share.py revoke {entry['token']})")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    reg = load_registry()
    if not reg["shares"]:
        print("no shares")
        return 0
    now = datetime.now(timezone.utc)
    for entry in sorted(reg["shares"], key=lambda e: e["expires"]):
        state = "EXPIRED" if is_expired(entry, now) else "active"
        albums = ", ".join(entry.get("albums") or [entry.get("album", "")])
        print(f"{state:7} {entry['expires']}  {entry['photos']:3} fotos  "
              f"{entry['label']}  [{albums}]")
        print(f"        {share_url(entry['token'])}")
    return 0


def find(reg: dict, token: str) -> dict:
    for entry in reg["shares"]:
        if entry["token"] == token:
            return entry
    raise SystemExit(f"unknown token: {token}")


def refresh_share(entry: dict) -> bool:
    """Re-copy index and web assets so an existing share picks up the current design.

    Photos, manifest and the filtered albums.json stay untouched: the link keeps
    its token, its expiry and its exact photo set.
    """
    dst = SHARES / entry["token"]
    if not dst.is_dir():
        return False
    albums = entry.get("albums") or []
    gallery_slug = albums[0] if len(albums) == 1 else "all"
    (dst / "index.html").write_text(
        make_index(entry["label"], gallery_slug, entry.get("sub", ""), entry.get("url", "")))
    for asset in ("viewer.js", "app.js", "style.css"):
        source = WEB / asset
        if source.exists():
            shutil.copy2(source, dst / asset)
    for path in [dst, *dst.rglob("*")]:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    return True


def cmd_refresh(_: argparse.Namespace) -> int:
    reg = load_registry()
    refreshed = sum(1 for entry in reg["shares"] if refresh_share(entry))
    print(f"refreshed {refreshed} of {len(reg['shares'])} share(s)")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    entry = revoke_share(args.token)
    print(f"revoked {entry['label']} ({share_url(args.token)})")
    return 0


def cmd_extend(args: argparse.Namespace) -> int:
    reg = load_registry()
    entry = find(reg, args.token)
    expires = parse_expiry(args.expires, args.days)
    if expires <= datetime.now(timezone.utc):
        raise SystemExit("new expiry is in the past")
    entry["expires"] = iso(expires)
    save_registry(reg)
    print(f"{entry['label']}: expires {entry['expires']}")
    return 0


def cmd_prune(_: argparse.Namespace) -> int:
    reg = load_registry()
    now = datetime.now(timezone.utc)
    keep, gone = [], []
    for entry in reg["shares"]:
        if is_expired(entry, now):
            shutil.rmtree(SHARES / entry["token"], ignore_errors=True)
            gone.append(entry)
        elif (SHARES / entry["token"]).is_dir():
            keep.append(entry)
        else:  # directory removed by hand
            gone.append(entry)
    if gone:
        reg["shares"] = keep
        save_registry(reg)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{stamp} prune: {len(gone)} removed, {len(keep)} active")
    for entry in gone:
        print(f"  removed {entry['label']} [{entry['album']}] exp {entry['expires']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a share for one or more albums")
    c.add_argument("--album", dest="albums", action="append", required=True,
                   help="album slug; repeat for several albums in one share")
    c.add_argument("--label", required=True)
    c.add_argument("--sub", default="")
    c.add_argument("--url", default="", help="link on the album title (e.g. the event page)")
    c.add_argument("--from", dest="source_build", default="build,build-private",
                   help="comma-separated build dirs; photos of the album are collected from both")
    c.add_argument("--days", type=int, default=None, help=f"lifetime (default {DEFAULT_DAYS})")
    c.add_argument("--expires", default=None, help="last day, YYYY-MM-DD")
    c.set_defaults(func=cmd_create)

    sub.add_parser("list", help="list shares and expiry").set_defaults(func=cmd_list)
    sub.add_parser("prune", help="delete expired shares").set_defaults(func=cmd_prune)
    sub.add_parser("refresh", help="re-copy index + web assets of active shares").set_defaults(func=cmd_refresh)

    r = sub.add_parser("revoke", help="delete a share now")
    r.add_argument("token")
    r.set_defaults(func=cmd_revoke)

    e = sub.add_parser("extend", help="change a share's expiry")
    e.add_argument("token")
    e.add_argument("--days", type=int, default=None)
    e.add_argument("--expires", default=None)
    e.set_defaults(func=cmd_extend)

    args = ap.parse_args()
    if args.cmd == "extend" and args.days is None and args.expires is None:
        args.days = DEFAULT_DAYS
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
