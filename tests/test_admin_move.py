import importlib.util
import json
import os
import subprocess
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
Path(STATE.name, "admin.json").write_text(json.dumps({
    "username": "test", "secret_key": "x" * 64,
    "password_hash": "scrypt$16384$8$1$00$00",
}) + "\n")
sys.path.insert(0, str(ROOT / "admin"))
import app  # noqa: E402


class MoveRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["auth"] = True
            session["csrf"] = "token"
        self.choices = [{"slug": "ziel", "label": "Ziel", "is_private": False, "mixed": False}]
        self.patches = [
            mock.patch.object(app, "album_choices", return_value=self.choices),
            mock.patch.object(app.library, "move_photos",
                              return_value={"public_touched": True}),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def post(self):
        return self.client.post(
            "/admin/photo/album",
            data={"csrf": "token", "ids": ["20261103-120000"], "album": "ziel",
                  "back": "album:test"},
            headers={"X-Requested-With": "fetch"},
        )

    def test_public_deploy_happens_before_a_failing_score(self):
        order = []

        def run(command, **kwargs):
            if "covers.py" in command[-1]:
                order.append("covers")
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "score.py" in command[-1]:
                order.append("score")
                return mock.Mock(returncode=1, stdout="bad score", stderr="")
            raise AssertionError(command)

        def deploy(skip_covers=False, allow_empty=False):
            order.append("deploy")
            return True, "OK"

        with mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", side_effect=deploy):
            response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order[:2], ["covers", "deploy"])
        self.assertIn("score", order)

    def test_deploy_failure_is_reported_with_recovery_hint(self):
        def run(command, **kwargs):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", return_value=(False, "down")), \
             mock.patch.object(app, "flash") as flash:
            self.post()
        flash.assert_called_once()
        text, category = flash.call_args.args
        self.assertIn("Deploy", text)
        self.assertIn("Lokale Änderung bleibt", text)
        self.assertEqual(category, "error")

    def test_cover_timeout_still_reaches_privacy_deploy(self):
        def run(command, **kwargs):
            if "covers.py" in command[-1]:
                raise subprocess.TimeoutExpired(command, 120)
            return mock.Mock(returncode=0, stdout="", stderr="")

        deploy = mock.Mock(return_value=(True, "OK"))
        with mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", deploy), \
             mock.patch.object(app, "flash") as flash:
            self.post()
        self.assertGreaterEqual(deploy.call_count, 1)
        self.assertIn("Cover", flash.call_args.args[0])

    def test_deploy_timeout_is_retried_and_reported(self):
        timeout = subprocess.TimeoutExpired(["deploy.py"], 120)
        with mock.patch.object(app.library, "deploy", side_effect=timeout) as deploy:
            ok, message = app.deploy_with_retry()
        self.assertFalse(ok)
        self.assertEqual(deploy.call_count, 2)
        self.assertIn("TimeoutExpired", message)

    def test_cover_cleanup_failure_still_reaches_deploy(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "build" / "covers").mkdir(parents=True)
        (root / "build" / "covers" / "x.jpg").write_bytes(b"x")
        def run(command, **kwargs):
            if "covers.py" in command[-1]:
                return mock.Mock(returncode=1, stdout="", stderr="cover failed")
            return mock.Mock(returncode=0, stdout="", stderr="")

        deploy = mock.Mock(return_value=(True, "OK"))
        with mock.patch.object(app, "REPO", root), \
             mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", deploy), \
             mock.patch("pathlib.Path.unlink", side_effect=OSError("locked")), \
             mock.patch.object(app, "flash"):
            self.post()
        self.assertGreaterEqual(deploy.call_count, 1)
        tmp.cleanup()

    def test_cover_cleanup_total_failure_deploys_without_covers(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "build" / "covers").mkdir(parents=True)
        (root / "build" / "covers" / "x.jpg").write_bytes(b"x")
        def run(command, **kwargs):
            if "covers.py" in command[-1]:
                return mock.Mock(returncode=1, stdout="", stderr="cover failed")
            return mock.Mock(returncode=0, stdout="", stderr="")

        deploy = mock.Mock(return_value=(True, "OK"))
        with mock.patch.object(app, "REPO", root), \
             mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", deploy), \
             mock.patch("pathlib.Path.unlink", side_effect=OSError("locked")), \
             mock.patch("pathlib.Path.rename", side_effect=OSError("readonly")), \
             mock.patch.object(app, "flash"):
            self.post()
        self.assertTrue(deploy.call_args.kwargs.get("skip_covers"))
        tmp.cleanup()

    def test_marker_write_failure_still_deploys_without_covers(self):
        def run(command, **kwargs):
            if "covers.py" in command[-1]:
                return mock.Mock(returncode=1, stdout="", stderr="cover failed")
            return mock.Mock(returncode=0, stdout="", stderr="")

        deploy = mock.Mock(return_value=(True, "OK"))
        with mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app.library, "mark_covers_unsafe",
                               side_effect=OSError("state unavailable")), \
             mock.patch.object(app, "deploy_with_retry", deploy), \
             mock.patch.object(app, "flash"):
            self.post()
        self.assertTrue(deploy.call_args.kwargs.get("skip_covers"))

    def test_score_timeout_preserves_deploy_recovery_hint(self):
        def run(command, **kwargs):
            if "score.py" in command[-1]:
                raise subprocess.TimeoutExpired(command, 600)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(app.subprocess, "run", side_effect=run), \
             mock.patch.object(app, "deploy_with_retry", return_value=(False, "down")), \
             mock.patch.object(app, "flash") as flash:
            self.post()
        text, category = flash.call_args.args
        self.assertIn("Score", text)
        self.assertIn("Deploy", text)
        self.assertIn("Lokale Änderung bleibt", text)
        self.assertEqual(category, "error")

    def test_mixed_target_preserves_visibility(self):
        self.choices[0]["mixed"] = True
        with mock.patch.object(app.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(app, "deploy_with_retry", return_value=(True, "OK")), \
             mock.patch.object(app, "flash"):
            self.post()
        app.library.move_photos.assert_called_with(["20261103-120000"], "ziel", None)


if __name__ == "__main__":
    unittest.main()
