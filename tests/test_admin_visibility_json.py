import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if importlib.util.find_spec("flask") is None:
    raise unittest.SkipTest("admin route tests need the .venv-admin Flask install")

ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory()
os.environ["RP_SHARES_ROOT"] = STATE.name
os.environ["RP_REPO"] = str(ROOT)
os.environ["RP_DEPLOY"] = "0"
(STATE_PATH := Path(STATE.name)).mkdir(exist_ok=True)
(STATE_PATH / "admin.json").write_text(json.dumps({
    "username": "test",
    "secret_key": "x" * 64,
    "password_hash": "scrypt$16384$8$1$00$00",
}) + "\n")
sys.path.insert(0, str(ROOT / "admin"))
import app  # noqa: E402


class PhotoVisibilityJsonTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["auth"] = True
            session["csrf"] = "token"

    def post(self):
        return self.client.post(
            "/admin/photo/visibility",
            data={"csrf": "token", "id": "20261103-120000", "mode": "public",
                  "album": "forum", "back": "album:forum"},
            headers={"X-Requested-With": "fetch"},
        )

    def test_json_response_carries_tree_and_album_counts(self):
        result = {"changed": True, "from": "private", "to": "public",
                  "deploy": (True, "OK")}
        summary = {"count": 2, "public": 1, "private": 1, "published": 1,
                   "is_private": False, "mixed": True}
        with mock.patch.object(app.library, "set_photo_visibility", return_value=result), \
             mock.patch.object(app, "album_summary", return_value=summary):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tree"], "public")
        self.assertEqual(payload["album"]["public"], 1)
        self.assertEqual(payload["album"]["private"], 1)
        self.assertTrue(payload["deploy_ok"])

    def test_json_error_is_reported_without_a_redirect(self):
        with mock.patch.object(app.library, "set_photo_visibility",
                               side_effect=ValueError("photo not found")):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["ok"])
        self.assertIn("photo not found", response.get_json()["error"])

    def test_non_json_post_still_redirects(self):
        result = {"changed": False, "tree": "public"}
        with mock.patch.object(app.library, "set_photo_visibility", return_value=result), \
             mock.patch.object(app, "album_summary", return_value=None):
            response = self.client.post(
                "/admin/photo/visibility",
                data={"csrf": "token", "id": "20261103-120000", "mode": "public",
                      "album": "forum", "back": "album:forum"},
            )
        self.assertEqual(response.status_code, 302)


class AlbumCoverRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["auth"] = True
            session["csrf"] = "token"
        self.detail = {"photos": [{"id": "20261103-120000", "tree": "public"}]}

    def test_public_photo_is_saved_as_pin(self):
        with mock.patch.object(app, "album_detail", return_value=self.detail), \
             mock.patch.object(app.subprocess, "run") as run, \
             mock.patch.object(app.library, "deploy", return_value=(True, "OK")):
            run.return_value.returncode = 0
            response = self.client.post(
                "/admin/cover",
                data={"csrf": "token", "id": "20261103-120000", "album": "forum",
                      "back": "album:forum"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app.load_cover_pins(), {"forum": "20261103-120000"})

    def test_private_photo_is_rejected(self):
        detail = {"photos": [{"id": "20261103-120000", "tree": "private"}]}
        with mock.patch.object(app, "album_detail", return_value=detail), \
             mock.patch.object(app.subprocess, "run") as run:
            response = self.client.post(
                "/admin/cover",
                data={"csrf": "token", "id": "20261103-120000", "album": "forum",
                      "back": "album:forum"},
            )
        self.assertEqual(response.status_code, 302)
        run.assert_not_called()
        self.assertNotEqual(app.load_cover_pins().get("forum"), "20261103-120000")


if __name__ == "__main__":
    unittest.main()
