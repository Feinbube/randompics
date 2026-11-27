#!/usr/bin/env python3
"""randompics admin: login, visibility, share links, photo removal.

Runs on 127.0.0.1 only; Apache proxies https://randompics.de/admin/ here.
Files are owned by hermes, so no sudo and no www-data write access is needed.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session)

ADMIN_DIR = Path(__file__).resolve().parent
CODE = ADMIN_DIR.parent
REPO = Path(os.environ.get("RP_REPO", CODE))
sys.path.insert(0, str(CODE / "pipeline"))
import library  # noqa: E402
import publish  # noqa: E402
import share as shares  # noqa: E402
from security import verify_password  # noqa: E402
from group_labels import similar_labels as _similar_labels  # noqa: E402

PREFIX = "/admin"
STATE = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
SBS_PYTHON = Path("/home/hermes/projects/sbs3d/.venv/bin/python")
UPLOAD_DIR = STATE / "uploads"
CONFIG_FILE = STATE / "admin.json"
AUDIT_FILE = STATE / "admin-audit.log"
DISMISS_FILE = STATE / "duplicate-ignore.json"
DEPTH_FILE = STATE / "depth-issues.json"
PHOTO_ID = re.compile(r"^\d{8}-\d{6}$")
MEDIA_ROOTS = {"public": REPO / "build" / "photos",
               "private": REPO / "build-private" / "photos"}
MEDIA_FILES = {"scene.jpg", "sbs.jpg", "depth.jpg", "thumb.jpg", "meta.json"}
MAX_SHARE_DAYS = 365

if not CONFIG_FILE.exists():
    raise SystemExit(f"missing {CONFIG_FILE} — run admin/manage.py set-password")

CONFIG = json.loads(CONFIG_FILE.read_text())
SECURE_COOKIE = os.environ.get("RP_ADMIN_COOKIE_SECURE", "1") != "0"

app = Flask(__name__, static_folder=None, template_folder=str(ADMIN_DIR / "templates"))
app.secret_key = CONFIG["secret_key"]
app.config.update(
    SESSION_COOKIE_NAME="randompics_admin",
    SESSION_COOKIE_PATH=PREFIX,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=SECURE_COOKIE,
    PERMANENT_SESSION_LIFETIME=12 * 3600,
    MAX_CONTENT_LENGTH=512 * 1024 * 1024,
)

ATTEMPTS: list[float] = []          # global: only one admin, all requests come via the proxy
WINDOW, LIMIT = 15 * 60, 5
UPLOAD_LOCK = threading.Lock()      # one render batch at a time
DEPTH_LOCK = threading.Lock()       # one read-modify-write of depth marks at a time


def audit(action: str, detail: str) -> None:
    line = (f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"{CONFIG.get('username', '?')} {action} {detail}\n")
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a") as handle:
            handle.write(line)
    except OSError:
        pass


def rate_limited() -> bool:
    now = time.time()
    ATTEMPTS[:] = [t for t in ATTEMPTS if now - t < WINDOW]
    return len(ATTEMPTS) >= LIMIT


def authed() -> bool:
    return bool(session.get("auth"))


def csrf_ok() -> bool:
    sent = request.form.get("csrf", "")
    expected = session.get("csrf", "")
    return bool(expected) and hmac.compare_digest(sent, expected)


def protected(view):
    def wrapper(*args, **kwargs):
        if not authed():
            return redirect(f"{PREFIX}/login")
        if request.method == "POST" and not csrf_ok():
            abort(400, "CSRF")
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def album_labels() -> dict:
    try:
        return json.loads((REPO / "web" / "albums.json").read_text())
    except OSError:
        return {}


def fmt_expiry(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def photo_stamp(pid: str) -> str:
    """Render the id's timestamp for humans: 20261102-223636 -> 02.11.2026 22:36."""
    if not PHOTO_ID.match(pid):
        return pid
    return f"{pid[6:8]}.{pid[4:6]}.{pid[0:4]} {pid[9:11]}:{pid[11:13]}"


def album_url(slug: str) -> str:
    return f"{PREFIX}/album/{slug}"


def back_url(default: str | None = None) -> str:
    """Return to the album/duplicates/depth page that submitted the action."""
    default = default or f"{PREFIX}/"
    raw = request.form.get("back", "")
    if raw == "duplicates":
        return f"{PREFIX}/duplicates"
    if raw == "depth":
        return f"{PREFIX}/depth"
    if raw == "similar":
        return f"{PREFIX}/similar"
    if re.fullmatch(r"album:[a-z0-9-]{1,80}", raw):
        return album_url(raw[6:])
    return default


def load_depth_issues() -> dict[str, dict]:
    try:
        data = json.loads(DEPTH_FILE.read_text())
        if not isinstance(data, dict):
            return {}
        issues = data.get("issues", {})
        if not isinstance(issues, dict):
            return {}
        clean: dict[str, dict] = {}
        for pid, value in issues.items():
            if not PHOTO_ID.match(str(pid)) or not isinstance(value, dict):
                continue
            note = value.get("note", "")
            updated = value.get("updated", "")
            clean[str(pid)] = {
                "note": (note if isinstance(note, str) else str(note))[:500],
                "updated": updated if isinstance(updated, str) else "",
            }
        return clean
    except (OSError, ValueError, TypeError):
        return {}


def save_depth_issues(issues: dict[str, dict]) -> None:
    DEPTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEPTH_FILE.with_name(f".{DEPTH_FILE.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"issues": issues}, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(DEPTH_FILE)


def fmt_updated(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _photo_rows(entry: dict, issues: dict[str, dict] | None = None) -> list[dict]:
    issues = load_depth_issues() if issues is None else issues
    private_ids = {row["id"] for row in entry["private"]}
    photos = [{"id": row["id"],
               "stamp": photo_stamp(row["id"]),
               "tree": "private" if row["id"] in private_ids else "public",
               "score": row.get("score"),
               "depth_issue": row["id"] in issues,
               "depth_note": (issues.get(row["id"]) or {}).get("note", "")}
              for row in entry["private"] + entry["public"]]
    photos.sort(key=lambda p: ((p["score"] or {}).get("composite", 99.0), p["id"]))
    return photos


def _share_rows(entry: dict) -> list[dict]:
    return [{"token": share["token"], "url": shares.share_url(share["token"]),
             "expires": fmt_expiry(share["expires"]), "label": share.get("label", "")}
            for share in entry["shares"]]


def _album(slug: str, entry: dict, meta: dict, with_photos: bool = False) -> dict:
    private_ids = {row["id"] for row in entry["private"]}
    rows = entry["public"] + entry["private"]
    cover = None
    if rows:
        row = min(rows, key=lambda item: ((item.get("score") or {}).get("composite", 99.0),
                                          item["id"]))
        cover = {"id": row["id"],
                 "tree": "private" if row["id"] in private_ids else "public"}
    return {
        "slug": slug,
        "label": meta.get("label", slug),
        "sub": meta.get("sub", ""),
        "url": meta.get("url", ""),
        "count": entry["count"],
        "public": len(entry["public"]),
        "private": len(entry["private"]),
        "is_private": entry["is_private"],
        "mixed": entry.get("mixed", False),
        "gate": entry.get("gate", 0.0),
        "published": entry.get("published", 0),
        "shares": _share_rows(entry),
        "cover": cover,
        "photos": _photo_rows(entry, load_depth_issues()) if with_photos else [],
    }


def album_list() -> list[dict]:
    labels = album_labels()
    return [_album(slug, entry, labels.get(slug, {}))
            for slug, entry in library.catalog().items()]


def album_detail(slug: str) -> dict:
    catalog = library.catalog()
    entry = catalog.get(slug)
    if entry is None:
        abort(404)
    assert entry is not None
    return _album(slug, entry, album_labels().get(slug, {}), with_photos=True)


def load_dismissed() -> set[tuple[str, str]]:
    try:
        raw = json.loads(DISMISS_FILE.read_text())
        return {(str(pair[0]), str(pair[1]))
                for pair in raw if len(pair) == 2}
    except (OSError, ValueError, TypeError):
        return set()


def save_dismissed(pairs: set[tuple[str, str]]) -> None:
    DISMISS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DISMISS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted([list(pair) for pair in pairs]), indent=1) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(DISMISS_FILE)


def duplicate_data() -> tuple[list[dict], int, int]:
    """Return open pairs, the count of stale pairs, and dismissed count."""
    scores = publish.load_scores()
    tree_of: dict[str, str] = {}
    album_of: dict[str, str] = {}
    for name, tree in library.TREES.items():
        for row in library.load_manifest(tree):
            tree_of[row["id"]] = name
            album_of[row["id"]] = row.get("album", "forum")
    labels = album_labels()
    dismissed = load_dismissed()
    depth_issues = load_depth_issues()

    seen: set[tuple[str, str]] = set()
    pairs: list[dict] = []
    stale = 0
    for pid, row in scores.items():
        near = row.get("nearest")
        try:
            similarity = float(row.get("similarity") or 0)
        except (TypeError, ValueError):
            similarity = 0.0
        if not near or str(pid) == str(near) or similarity < 0.90:
            continue
        key = tuple(sorted((str(pid), str(near))))
        if key in seen:
            continue
        seen.add(key)
        if pid not in tree_of or near not in tree_of:
            stale += 1
            continue
        if key in dismissed:
            continue

        first, second = key
        first_score = (scores.get(first) or {}).get("composite", 0) or 0
        second_score = (scores.get(second) or {}).get("composite", 0) or 0
        if abs(first_score - second_score) < 1e-9:
            keep, drop = None, None
            label_a = label_b = "gleich"
        else:
            keep, drop = (first, second) if first_score > second_score else (second, first)
            label_a = "behalten" if first == keep else "schwächer"
            label_b = "behalten" if second == keep else "schwächer"
        album_a = album_of.get(first, "")
        album_b = album_of.get(second, "")
        pairs.append({
            "a_photo": {"id": first, "stamp": photo_stamp(first), "tree": tree_of[first],
                        "score": scores.get(first),
                        "album": album_a,
                        "album_label": labels.get(album_a, {}).get("label", album_a),
                        "depth_issue": first in depth_issues,
                        "depth_note": (depth_issues.get(first) or {}).get("note", "")},
            "b_photo": {"id": second, "stamp": photo_stamp(second), "tree": tree_of[second],
                        "score": scores.get(second),
                        "album": album_b,
                        "album_label": labels.get(album_b, {}).get("label", album_b),
                        "depth_issue": second in depth_issues,
                        "depth_note": (depth_issues.get(second) or {}).get("note", "")},
            "album_a": album_a,
            "album_b": album_b,
            "album_label_a": labels.get(album_a, {}).get("label", album_a),
            "album_label_b": labels.get(album_b, {}).get("label", album_b),
            "label_a": label_a,
            "label_b": label_b,
            "keep": keep,
            "drop": drop,
            "sim": round(similarity * 100, 1),
        })
    pairs.sort(key=lambda pair: -pair["sim"])
    return pairs, stale, len(dismissed)


def depth_data() -> tuple[list[dict], list[dict], int]:
    """Return manually marked photos, stereo photos without a depth map, and stale marks."""
    catalog = library.catalog()
    labels = album_labels()
    issues = load_depth_issues()
    manual: list[dict] = []
    auto: list[dict] = []
    seen: set[str] = set()

    for slug, entry in catalog.items():
        album_label = labels.get(slug, {}).get("label", slug)
        for tree in ("public", "private"):
            for row in entry.get(tree, []):
                pid = row["id"]
                seen.add(pid)
                has_depth = (MEDIA_ROOTS[tree] / pid / "depth.jpg").is_file()
                issue = issues.get(pid)
                item = {
                    "id": pid,
                    "stamp": photo_stamp(pid),
                    "tree": tree,
                    "album_slug": slug,
                    "album_label": album_label,
                    "score": row.get("score"),
                    "has_depth": has_depth,
                    "note": (issue or {}).get("note", ""),
                    "updated": (issue or {}).get("updated", ""),
                    "updated_label": fmt_updated((issue or {}).get("updated", "")),
                    "depth_issue": bool(issue),
                    "depth_note": (issue or {}).get("note", ""),
                }
                if issue:
                    manual.append({**item, "manual": True, "auto": False})
                elif not has_depth:
                    auto.append({**item, "manual": False, "auto": True})

    stale = sum(1 for pid in issues if pid not in seen)
    manual.sort(key=lambda item: (item["updated"], item["id"]), reverse=True)
    auto.sort(key=lambda item: (item["album_label"], item["stamp"], item["id"]))
    return manual, auto, stale


def similar_groups() -> tuple[list[dict], int, int]:
    """Cluster the duplicate pairs into connected groups; one keeper per group."""
    pairs, stale, _dismissed = duplicate_data()
    parent: dict[str, str] = {}
    by_id: dict[str, dict] = {}

    def find(pid: str) -> str:
        parent.setdefault(pid, pid)
        while parent[pid] != pid:
            parent[pid] = parent[parent[pid]]
            pid = parent[pid]
        return pid

    def union(first: str, second: str) -> None:
        left, right = find(first), find(second)
        if left != right:
            parent[right] = left

    for pair in pairs:
        for key in ("a_photo", "b_photo"):
            photo = pair[key]
            by_id[photo["id"]] = photo
        union(pair["a_photo"]["id"], pair["b_photo"]["id"])

    members: dict[str, list[str]] = {}
    for pid in by_id:
        members.setdefault(find(pid), []).append(pid)

    groups = []
    for ids in members.values():
        if len(ids) < 2:
            continue
        photos = [by_id[pid] for pid in ids]
        keeper_id, labels, tie = _similar_labels(photos)
        photos.sort(key=lambda photo: (
            photo["id"] != keeper_id,
            -((photo.get("score") or {}).get("composite") or -1),
            photo["id"]))
        group_sim = max(pair["sim"] for pair in pairs
                        if pair["a_photo"]["id"] in ids and pair["b_photo"]["id"] in ids)
        groups.append({"photos": photos, "keeper": keeper_id,
                       "labels": labels, "sim": group_sim, "tie": tie})
    groups.sort(key=lambda group: (-group["sim"], -len(group["photos"])))
    return groups, stale, len(pairs)


@app.get(f"{PREFIX}/healthz")
def healthz():
    return "ok"


@app.get(f"{PREFIX}/login")
def login_form():
    if authed():
        return redirect(f"{PREFIX}/")
    session["csrf"] = secrets.token_hex(16)
    return render_template("login.html", csrf=session["csrf"], error=None)


@app.post(f"{PREFIX}/login")
def login():
    if not csrf_ok():
        abort(400, "CSRF")
    if rate_limited():
        audit("login", "rate-limited")
        return render_template("login.html", csrf=session.get("csrf", ""),
                               error="Zu viele Versuche — in 15 Minuten erneut."), 429
    password = request.form.get("password", "")
    if not verify_password(password, CONFIG["password_hash"]):
        ATTEMPTS.append(time.time())
        audit("login", "failed")
        return render_template("login.html", csrf=session.get("csrf", ""),
                               error="Passwort falsch."), 401
    ATTEMPTS.clear()
    session.clear()
    session["auth"] = True
    session["csrf"] = secrets.token_hex(16)
    session.permanent = True
    audit("login", "ok")
    return redirect(f"{PREFIX}/")


@app.post(f"{PREFIX}/logout")
@protected
def logout():
    audit("logout", "")
    session.clear()
    return redirect(f"{PREFIX}/login")


@app.get(f"{PREFIX}/")
@protected
def dashboard():
    albums = album_list()
    duplicate_pairs, duplicate_stale, dismissed = duplicate_data()
    depth_manual, depth_auto, depth_stale = depth_data()
    return render_template(
        "dashboard.html", albums=albums, csrf=session["csrf"],
        user=CONFIG.get("username", ""),
        public_total=sum(album["public"] for album in albums),
        private_total=sum(album["private"] for album in albums),
        share_count=sum(len(album["shares"]) for album in albums),
        duplicate_total=len(duplicate_pairs), duplicate_stale=duplicate_stale,
        dismissed=dismissed,
        depth_manual=len(depth_manual), depth_auto=len(depth_auto),
        depth_stale=depth_stale,
    )


@app.get(f"{PREFIX}/album/<slug>")
@protected
def album_page(slug: str):
    album = album_detail(slug)
    return render_template("album.html", album=album, csrf=session["csrf"],
                           user=CONFIG.get("username", ""))


@app.get(f"{PREFIX}/duplicates")
@protected
def duplicates_page():
    pairs, stale, dismissed = duplicate_data()
    return render_template("duplicates.html", duplicates=pairs,
                           duplicate_total=len(pairs), duplicate_stale=stale,
                           dismissed=dismissed, csrf=session["csrf"],
                           user=CONFIG.get("username", ""))


@app.get(f"{PREFIX}/depth")
@protected
def depth_page():
    manual, auto, stale = depth_data()
    return render_template("depth.html", manual=manual, auto=auto, stale=stale,
                           csrf=session["csrf"], user=CONFIG.get("username", ""))


@app.get(f"{PREFIX}/similar")
@protected
def similar_page():
    groups, stale, pair_total = similar_groups()
    return render_template("similar.html", groups=groups, stale=stale,
                           pair_total=pair_total, csrf=session["csrf"],
                           user=CONFIG.get("username", ""))


@app.post(f"{PREFIX}/depth/flag")
@protected
def depth_flag():
    pid = request.form.get("id", "")
    action = request.form.get("action", "flag")
    if not PHOTO_ID.match(pid) or action not in {"flag", "clear"}:
        abort(400)
    with DEPTH_LOCK:
        issues = load_depth_issues()
        note = ""
        if action == "clear":
            issues.pop(pid, None)
            flagged = False
        else:
            note = request.form.get("note", "").strip()[:500]
            issues[pid] = {"note": note,
                           "updated": datetime.now().astimezone().isoformat(timespec="seconds")}
            flagged = True
        save_depth_issues(issues)
    audit("depth", f"{pid} {action} note={note.replace(chr(10), ' ')[:200]!r}")
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "flagged": flagged, "note": note})
    flash("Tiefenproblem gespeichert." if flagged else "Tiefenproblem entfernt.", "ok")
    return redirect(back_url(f"{PREFIX}/depth"))


@app.post(f"{PREFIX}/duplicates/dismiss")
@protected
def dismiss_duplicate():
    first = request.form.get("a", "")
    second = request.form.get("b", "")
    if not PHOTO_ID.match(first) or not PHOTO_ID.match(second) or first == second:
        abort(400)
    pairs = load_dismissed()
    pairs.add(tuple(sorted((first, second))))
    save_dismissed(pairs)
    audit("duplicate-dismiss", f"{first} {second}")
    flash("Paar ausgeblendet.", "ok")
    return redirect(f"{PREFIX}/duplicates")


@app.post(f"{PREFIX}/duplicates/reset")
@protected
def reset_dismissed():
    save_dismissed(set())
    audit("duplicate-dismiss-reset", "")
    flash("Ausblendungen zurückgesetzt.", "ok")
    return redirect(f"{PREFIX}/duplicates")


@app.post(f"{PREFIX}/upload")
@protected
def upload():
    album = request.form.get("album", "")
    catalog = library.catalog()
    entry = catalog.get(album)
    if entry is None:
        flash(f"Album nicht gefunden: {album}", "error")
        return redirect(f"{PREFIX}/")
    if not UPLOAD_LOCK.acquire(blocking=False):
        flash("Es läuft bereits ein Upload. Bitte warte, bis er fertig ist.", "error")
        return redirect(album_url(album))
    try:
        requested = request.form.get("visibility", "auto")
        if requested not in {"auto", "private", "public"}:
            requested = "auto"
        if requested == "auto":
            visibility = "private" if entry.get("is_private") or entry.get("mixed") else "public"
        else:
            visibility = requested

        files = [item for item in request.files.getlist("files") if item and item.filename]
        files = [item for item in files if item.filename.lower().endswith((".jpg", ".jpeg"))]
        if not files:
            flash("Keine JPEG-Dateien ausgewählt.", "error")
            return redirect(album_url(album))
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        batch = secrets.token_hex(4)
        saved = []
        for index, item in enumerate(files):
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(item.filename).name)
            target = UPLOAD_DIR / f"{batch}-{index:02d}-{name}"
            item.save(target)
            saved.append(target)
        result = None
        try:
            result = subprocess.run(
                [str(SBS_PYTHON), str(CODE / "pipeline" / "add_photo.py"),
                 "--album", album, "--visibility", visibility,
                 *[str(path) for path in saved]],
                capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            audit("upload", f"{album} timeout files={len(saved)}")
            flash("Upload: Zeitüberschreitung beim Rendern.", "error")
            return redirect(album_url(album))
        finally:
            for path in saved:  # originals are copied into source/ by add_photo.py
                path.unlink(missing_ok=True)
        ok = result.returncode == 0
        lines = (result.stdout or result.stderr).strip().splitlines()
        tail = lines[-1] if lines else ""
        payload = {}
        if tail.startswith("{"):
            try:
                parsed = json.loads(tail)
                if isinstance(parsed, dict):
                    payload = parsed
            except ValueError:
                payload = {}
        audit("upload", f"{album} {visibility} files={len(saved)} rc={result.returncode}")
        visibility_label = "privat" if visibility == "private" else "öffentlich"
        if "ids" in payload:
            added = len(payload.get("ids") or [])
            skipped = len(payload.get("skipped") or [])
            summary = f"{added} hinzugefügt"
            if skipped:
                summary += f", {skipped} übersprungen (kein SBS-Paar)"
            summary += f", {visibility_label}"
            if payload.get("score_ok") is False:
                summary += ", Score FEHLER"
            if payload.get("deploy_ok") is False:
                summary += ", Deploy FEHLER"
                if payload.get("deploy_message"):
                    summary += f" ({payload['deploy_message']})"
            ok = ok and payload.get("score_ok") is not False and payload.get("deploy_ok") is not False
            detail = ""
        else:
            summary = f"{len(saved)} Datei(en), {visibility_label}"
            detail = f" · {tail}" if tail else ""
        flash(f"Upload {album}: {summary} — {'OK' if ok else 'FEHLER'}{detail}",
              "ok" if ok else "error")
        return redirect(album_url(album))
    finally:
        UPLOAD_LOCK.release()


@app.post(f"{PREFIX}/album/new")
@protected
def album_new():
    label = request.form.get("label", "").strip()
    sub = request.form.get("sub", "").strip()
    url = request.form.get("url", "").strip()
    private = request.form.get("private") == "1"
    if not label:
        flash("Albumname fehlt.", "error")
        return redirect(f"{PREFIX}/")
    created = library.create_album(label, sub, url, private)
    ok, message = True, "privat — kein Deploy nötig"
    if not private:
        ok, message = library.deploy()
    audit("album", f"new {created['slug']} private={private}")
    flash(f"Album angelegt: {label} ({created['slug']}, "
          f"{'privat' if private else 'öffentlich'}); Deploy {'OK' if ok else 'FEHLER'}: {message}",
          "ok" if ok else "error")
    return redirect(album_url(created["slug"]))


@app.post(f"{PREFIX}/album/rename")
@protected
def album_rename():
    slug = request.form.get("album", "")
    label = request.form.get("label", "").strip()
    sub = request.form.get("sub", "").strip()
    url = request.form.get("url", "").strip()
    back = back_url(album_url(slug) if slug else f"{PREFIX}/")
    if not label:
        flash("Albumname fehlt.", "error")
        return redirect(back)
    try:
        result = library.rename_album(slug, label, sub, url)
    except (ValueError, OSError) as exc:
        flash(f"Umbenennen fehlgeschlagen: {exc}", "error")
        return redirect(back)
    deployed, message = library.deploy()
    audit("album-rename", f"{slug} {result['old'].get('label')!r} -> {label!r} rc={deployed}")
    flash(f"{slug}: umbenannt in „{label}“"
          + (f"; Deploy {'OK' if deployed else 'FEHLER'}: {message}" if deployed else f"; {message}"),
          "ok" if deployed else "error")
    return redirect(back)


@app.post(f"{PREFIX}/gate")
@protected
def gate():
    album = request.form.get("album", "")
    back = back_url(album_url(album) if album else f"{PREFIX}/")
    try:
        minimum = float(request.form.get("min", "0"))
    except ValueError:
        minimum = 0.0
    minimum = max(0.0, min(5.0, minimum))
    gates = publish.load_gates()
    gates[album] = minimum
    publish.save_gates(gates)
    audit("gate", f"{album} min={minimum}")
    entry = library.catalog().get(album, {})
    ok, message = True, "privat — greift beim nächsten Link"
    if entry.get("public"):
        ok, message = library.deploy()
    flash(f"{album}: Mindest-Score {minimum:.1f} — Deploy {'OK' if ok else 'FEHLER'}: {message}",
          "ok" if ok else "error")
    return redirect(back)


@app.post(f"{PREFIX}/share")
@protected
def share_album():
    slug = request.form.get("album", "")
    back = back_url(album_url(slug) if slug else f"{PREFIX}/")
    try:
        days = int(request.form.get("days", "30"))
    except ValueError:
        days = 30
    days = max(1, min(days, MAX_SHARE_DAYS))
    catalog = library.catalog()
    entry = catalog.get(slug)
    if not entry:
        flash(f"Album nicht gefunden: {slug}", "error")
        return redirect(back)
    if not entry["count"]:
        flash("Album hat noch keine Fotos.", "error")
        return redirect(back)
    meta = album_labels().get(slug, {})
    try:
        created = shares.create_share([library.PUBLIC, library.PRIVATE], [slug],
                                      meta.get("label", slug), meta.get("sub", ""),
                                      meta.get("url", ""), days=days)
    except (ValueError, OSError) as exc:
        flash(f"Link fehlgeschlagen: {exc}", "error")
        return redirect(back)
    audit("share", f"{slug} -> {created['token']} {created['expires']}")
    flash(f"Link erzeugt: {shares.share_url(created['token'])} "
          f"(bis {fmt_expiry(created['expires'])})", "ok")
    return redirect(back)


@app.post(f"{PREFIX}/unshare")
@protected
def unshare():
    token = request.form.get("token", "")
    back = back_url()
    try:
        entry = shares.revoke_share(token)
    except SystemExit:
        flash("Link nicht gefunden.", "error")
        return redirect(back)
    audit("unshare", f"{token} ({entry.get('label', '')})")
    flash(f"Link widerrufen: {entry.get('label', token)}", "ok")
    return redirect(back)


@app.post(f"{PREFIX}/visibility")
@protected
def visibility():
    slug = request.form.get("album", "")
    mode = request.form.get("mode", "")
    back = back_url(album_url(slug) if slug else f"{PREFIX}/")
    if mode not in {"public", "private"}:
        abort(400)
    try:
        result = library.set_visibility(slug, mode == "private")
    except OSError as exc:
        flash(f"Fehler: {exc}", "error")
        return redirect(back)
    if not result.get("changed"):
        flash(f"{slug}: keine Änderung.", "error")
        return redirect(back)
    deployed, message = result.get("deploy", (True, ""))
    audit("visibility", f"{slug} -> {mode} moved={result['moved']} deploy={deployed}")
    state = "jetzt privat" if mode == "private" else "jetzt öffentlich"
    flash(f"{slug}: {result['moved']} Fotos verschoben, {state}. "
          f"Deploy {'OK' if deployed else 'FEHLER'}: {message}",
          "ok" if deployed else "error")
    return redirect(back)


@app.post(f"{PREFIX}/photo/visibility")
@protected
def photo_visibility():
    pid = request.form.get("id", "")
    mode = request.form.get("mode", "")
    album = request.form.get("album", "")
    back = back_url(album_url(album) if album else f"{PREFIX}/")
    if not PHOTO_ID.match(pid) or mode not in {"public", "private"}:
        abort(400)
    try:
        result = library.set_photo_visibility(pid, mode == "private")
    except (ValueError, OSError) as exc:
        flash(f"{pid}: {exc}", "error")
        return redirect(back)
    deployed, message = result.get("deploy", (True, ""))
    audit("photo-visibility", f"{pid} -> {mode} changed={result['changed']}")
    flash(f"{pid}: jetzt {'privat' if mode == 'private' else 'öffentlich'}"
          + (f" — Deploy {'OK' if deployed else 'FEHLER'}: {message}"
             if result["changed"] else ""),
          "ok" if deployed else "error")
    return redirect(back)


@app.post(f"{PREFIX}/remove")
@protected
def remove():
    pid = request.form.get("id", "")
    back = back_url()
    if not PHOTO_ID.match(pid):
        abort(400)
    try:
        result = library.remove_photo(pid, reason="admin-ui")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(back)
    with DEPTH_LOCK:
        issues = load_depth_issues()
        if pid in issues:
            issues.pop(pid)
            save_depth_issues(issues)
    deployed, message = result.get("deploy", (True, ""))
    audit("remove", f"{pid} tree={result['tree']} shares={','.join(result['shares'])}")
    source_note = "Quelle gelöscht" if result.get("source_deleted") else "keine Quelle gefunden"
    flash(f"{pid} endgültig gelöscht ({result['tree']}, {source_note}; "
          f"{len(result['shares'])} Link(s) bereinigt)"
          + (f"; Deploy {'OK' if deployed else 'FEHLER'}: {message}"
             if result["tree"] == "public" else ""),
          "ok" if deployed else "error")
    return redirect(back)


@app.get(f"{PREFIX}/viewer.js")
@protected
def viewer_js():
    response = send_file(REPO / "web" / "viewer.js", mimetype="text/javascript")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get(f"{PREFIX}/preview/<tree>/<pid>")
@protected
def preview(tree: str, pid: str):
    root = MEDIA_ROOTS.get(tree)
    if root is None or not PHOTO_ID.match(pid):
        abort(404)
    assert root is not None
    photo_dir = root / pid
    if not (photo_dir / "scene.jpg").is_file():
        abort(404)
    try:
        meta = json.loads((photo_dir / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    has_depth = (photo_dir / "depth.jpg").is_file()
    data = {
        "id": pid,
        "ok": has_depth,
        "single": not (photo_dir / "sbs.jpg").is_file(),
        "anchor": meta.get("anchor", 0.5),
        "tree_label": "öffentlich" if tree == "public" else "privat",
        "scene": f"{PREFIX}/media/{tree}/{pid}/scene.jpg",
        "depth": f"{PREFIX}/media/{tree}/{pid}/depth.jpg",
        "sbs": f"{PREFIX}/media/{tree}/{pid}/sbs.jpg",
    }
    return render_template("preview.html", pid=pid, tree=tree, data=data)


@app.get(f"{PREFIX}/media/<tree>/<pid>/<name>")
@protected
def media(tree: str, pid: str, name: str):
    root = MEDIA_ROOTS.get(tree)
    if root is None or not PHOTO_ID.match(pid) or name not in MEDIA_FILES:
        abort(404)
    assert root is not None
    path = root / pid / name
    if not path.is_file():
        abort(404)
    response = send_file(path, conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    return response


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="127.0.0.1", port=int(os.environ.get("RP_ADMIN_PORT", "8474")),
          threads=4, ident=None)
