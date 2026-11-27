#!/usr/bin/env python3
"""Manage the randompics admin account.

    manage.py set-password [--user frank] [--password ...]   # prompts if omitted
    manage.py status

Writes /srv/randompics/admin.json (0600): username, scrypt hash, session secret.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from security import hash_password  # noqa: E402

STATE = Path(os.environ.get("RP_SHARES_ROOT", "/srv/randompics"))
CONFIG = STATE / "admin.json"


def load() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def save(data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password")
    sp.add_argument("--user", default="frank")
    sp.add_argument("--password", default=None,
                    help="non-interactive; omit to be prompted")
    sub.add_parser("status")

    args = ap.parse_args()
    data = load()

    if args.cmd == "status":
        if not data:
            print("no account")
            return 1
        print(f"user: {data.get('username')}  file: {CONFIG}  exists: {CONFIG.exists()}")
        return 0

    password = args.password
    if not password:
        password = getpass.getpass("new password: ")
        if password != getpass.getpass("repeat: "):
            print("passwords differ", file=sys.stderr)
            return 1
    if len(password) < 10:
        print("use at least 10 characters", file=sys.stderr)
        return 1

    data.update({
        "username": args.user,
        "password_hash": hash_password(password),
        "secret_key": data.get("secret_key") or secrets.token_hex(32),
        "updated": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    save(data)
    print(f"OK: password set for {args.user} ({CONFIG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
