#!/usr/bin/env python3
"""Idempotent editor for ops-edit: add the private-share Alias to the
randompics.de TLS vhost and scope the public Cache-Control to the docroot.

ops-edit copies the vhost to a temp file and runs this script with that path.
"""

from __future__ import annotations

import sys
from pathlib import Path

ALIAS_MARK = "Alias /s/ /srv/randompics/shares/"
PROXY_MARK = "ProxyPass /admin/ http://127.0.0.1:8474/admin/"

VHOST_FILESMATCH = """    <IfModule mod_headers.c>
        <FilesMatch "\\.(jpg|jpeg|png|webp|json)$">
            Header set Cache-Control "public, max-age=3600"
        </FilesMatch>
    </IfModule>
"""

DOCROOT_BLOCK = """    <Directory /var/www/randompics.de>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
"""

DOCROOT_WITH_CACHE = """    <Directory /var/www/randompics.de>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted

        <IfModule mod_headers.c>
            <FilesMatch "\\.(jpg|jpeg|png|webp|json)$">
                Header set Cache-Control "public, max-age=3600"
            </FilesMatch>
        </IfModule>
    </Directory>
"""

PRIVATE_BLOCK = """    # Private album shares: unguessable expiring links, content outside the docroot.
    Alias /s/ /srv/randompics/shares/
    <Directory /srv/randompics/shares>
        Options -Indexes
        AllowOverride None
        Require all granted
        Header always set X-Robots-Tag "noindex, nofollow, noarchive, noimageindex"
        Header always set Referrer-Policy "no-referrer"
        Header always set Cache-Control "private, no-store"
    </Directory>

    # randompics admin: Flask on 127.0.0.1:8474, never exposed directly.
    ProxyPass /admin/ http://127.0.0.1:8474/admin/
    ProxyPassReverse /admin/ http://127.0.0.1:8474/admin/
    <Location /admin/>
        Require all granted
        Header always set X-Robots-Tag "noindex, nofollow, noarchive"
        Header always set Cache-Control "private, no-store"
        Header always set Referrer-Policy "no-referrer"
    </Location>

"""


def main() -> int:
    path = Path(sys.argv[1])
    text = path.read_text()

    if ALIAS_MARK in text and PROXY_MARK in text:
        print("already applied")
        return 0
    if text.count(VHOST_FILESMATCH) != 1:
        print(f"expected exactly one vhost-level FilesMatch block, found "
              f"{text.count(VHOST_FILESMATCH)}", file=sys.stderr)
        return 1
    if text.count(DOCROOT_BLOCK) != 1:
        print(f"expected exactly one docroot Directory block, found "
              f"{text.count(DOCROOT_BLOCK)}", file=sys.stderr)
        return 1

    text = text.replace(VHOST_FILESMATCH, "", 1)
    text = text.replace(DOCROOT_BLOCK, DOCROOT_WITH_CACHE, 1)
    marker = "    ErrorLog ${APACHE_LOG_DIR}/randompics.de-error.log"
    if text.count(marker) != 1:
        print("ErrorLog marker not found once", file=sys.stderr)
        return 1
    text = text.replace(marker, PRIVATE_BLOCK + marker, 1)

    path.write_text(text)
    print("patched: alias added, public Cache-Control scoped to the docroot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
