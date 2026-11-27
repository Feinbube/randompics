import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))

import albums  # noqa: E402
import library  # noqa: E402


class MovePhotosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.public = root / "build"
        self.private = root / "build-private"
        for tree in (self.public, self.private):
            (tree / "photos").mkdir(parents=True)
        (self.public / "photos" / "20260101-120000").mkdir()
        (self.private / "photos" / "20260101-120001").mkdir()
        self.overrides = root / "album_overrides.txt"
        self.overrides.write_text("20260101-120000 old\n")
        self.patches = [
            mock.patch.object(library, "TREES",
                              {"public": self.public, "private": self.private}),
            mock.patch.object(library, "PUBLIC", self.public),
            mock.patch.object(library, "PRIVATE", self.private),
            mock.patch.object(library, "OVERRIDES_FILE", self.overrides),
            mock.patch.object(albums, "OVERRIDES_FILE", self.overrides),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_private_photo_moves_to_public_target(self):
        result = library.move_photos(["20260101-120001"], "ciutat", False)
        self.assertTrue(result["public_touched"])
        self.assertTrue((self.public / "photos" / "20260101-120001").is_dir())
        self.assertFalse((self.private / "photos" / "20260101-120001").exists())
        self.assertIn("20260101-120001 ciutat", self.overrides.read_text())
        rows = json.loads((self.public / "manifest.json").read_text())
        moved = next(row for row in rows if row["id"] == "20260101-120001")
        self.assertEqual(moved["album"], "ciutat")

    def test_public_photo_moves_to_private_target(self):
        result = library.move_photos(["20260101-120000"], "old", True)
        self.assertTrue(result["public_touched"])
        self.assertTrue((self.private / "photos" / "20260101-120000").is_dir())
        self.assertFalse((self.public / "photos" / "20260101-120000").exists())

    def test_same_tree_move_only_changes_album(self):
        result = library.move_photos(["20260101-120000"], "other", False)
        self.assertFalse(result["tree_changed"])
        self.assertTrue(result["public_touched"])
        self.assertIn("20260101-120000 other", self.overrides.read_text())

    def test_mixed_target_preserves_each_visibility(self):
        result = library.move_photos(["20260101-120001", "20260101-120000"],
                                     "mixed", None)
        self.assertFalse(result["tree_changed"])
        self.assertTrue(result["public_touched"])
        self.assertTrue((self.private / "photos" / "20260101-120001").is_dir())
        self.assertTrue((self.public / "photos" / "20260101-120000").is_dir())
        text = self.overrides.read_text()
        self.assertIn("20260101-120001 mixed", text)
        self.assertIn("20260101-120000 mixed", text)

    def test_deploy_env_handles_unsafe_covers_and_empty_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "covers-unsafe.json"
            marker.write_text("{}\n")
            with mock.patch.object(library, "COVERS_UNSAFE", marker), \
                 mock.patch.object(library.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="OK", stderr="")
                ok, message = library.deploy(allow_empty=True)
            self.assertTrue(ok)
            env = run.call_args.kwargs["env"]
            self.assertEqual(env.get("RP_SKIP_COVERS"), "1")
            self.assertEqual(env.get("RP_ALLOW_EMPTY"), "1")

    def test_collision_is_rejected_before_moving(self):
        (self.private / "photos" / "20260101-120000").mkdir()
        with self.assertRaises(ValueError):
            library.move_photos(["20260101-120000"], "old", True)
        self.assertTrue((self.public / "photos" / "20260101-120000").is_dir())


if __name__ == "__main__":
    unittest.main()
