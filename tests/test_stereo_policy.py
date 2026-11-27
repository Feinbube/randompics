"""The collection holds only side-by-side stereo photos."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))

import albums  # noqa: E402
import build  # noqa: E402
from unittest.mock import patch

from render import NotStereo, has_camera_exif, is_sbs_size, render_photo, split_sbs  # noqa: E402


class StereoPolicyTest(unittest.TestCase):
    def test_sbs_signature_is_positive(self):
        self.assertTrue(is_sbs_size(7680, 2880))
        self.assertFalse(is_sbs_size(4080, 3072))
        self.assertFalse(is_sbs_size(4000, 2500))  # a wide ordinary panorama

    def test_camera_exif_is_not_a_beam_pro_sbs(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.jpg"
            camera = Path(tmp) / "camera.jpg"
            Image.new("RGB", (8, 8)).save(plain)
            exif = Image.Exif()
            exif[271] = "XREAL"
            exif[272] = "X4000"
            Image.new("RGB", (8, 8)).save(camera, exif=exif)
            self.assertFalse(has_camera_exif(plain))
            self.assertTrue(has_camera_exif(camera))

    def test_render_rejects_camera_exif_before_splitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("render.has_camera_exif", return_value=True), \
                 patch("render.cv2.imread", return_value=np.zeros((2, 2, 3), dtype=np.uint8)), \
                 patch("render.split_sbs", side_effect=AssertionError("must not split")):
                with self.assertRaises(NotStereo):
                    render_photo(Path(tmp) / "camera.jpg", Path(tmp) / "out")

    def test_split_rejects_ordinary_photo(self):
        with self.assertRaises(NotStereo):
            split_sbs(np.zeros((100, 150, 3), dtype=np.uint8))

    def test_run_one_does_not_keep_a_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "single.jpg"
            out = Path(tmp) / "out"
            cv2.imwrite(str(src), np.full((100, 150, 3), 127, dtype=np.uint8))
            row = build.run_one((src, out, None))
            self.assertTrue(row["non_sbs"])
            self.assertFalse(out.exists())

    def test_prune_singles_removes_only_flat_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            shots = Path(tmp)
            single = shots / "20240101-120000"
            pair = shots / "20240101-120001"
            single.mkdir()
            pair.mkdir()
            (single / "meta.json").write_text(json.dumps({"single": True, "depth": False}))
            (pair / "meta.json").write_text(json.dumps({"depth": True}))
            self.assertEqual(build.prune_singles(shots), {single.name})
            self.assertFalse(single.exists())
            self.assertTrue(pair.exists())

    def test_jobs_skips_non_sbs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            shots = Path(tmp) / "shots"
            source.mkdir()
            (source / "SV_20240101_120000.jpg").write_bytes(b"")
            self.assertEqual(build.jobs(source, shots, None, skip={"20240101-120000"}), [])

    def test_covers_for_every_album_with_rows(self):
        existing = [
            {"id": "20261102-153826", "album": "forum"},
            {"id": "20261101-121758", "album": "oceanografic"},
            {"id": "20261101-164119", "album": "oceanografic"},
            {"id": "20260609-165238", "album": "kamera-09-06-2026-11-06-2026"},
        ]
        covers = build.covers_for(existing, pins={})
        self.assertEqual(covers["forum"], "20261102-153826")
        self.assertEqual(covers["oceanografic"], "20261101-121758")
        self.assertEqual(covers["kamera-09-06-2026-11-06-2026"], "20260609-165238")

    def test_cover_pin_wins_and_invalid_pin_falls_back(self):
        existing = [
            {"id": "20261101-121758", "album": "oceanografic"},
            {"id": "20261101-164119", "album": "oceanografic"},
        ]
        pinned = build.covers_for(existing, pins={"oceanografic": "20261101-164119"})
        self.assertEqual(pinned["oceanografic"], "20261101-164119")
        fallback = build.covers_for(existing, pins={"oceanografic": "20260101-000000"})
        self.assertEqual(fallback["oceanografic"], "20261101-121758")

    def test_private_build_skips_public_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "build" / "photos" / "20260101-120000"
            private = root / "build-private"
            public.mkdir(parents=True)
            private.mkdir()
            self.assertEqual(build.public_photo_ids(private), {"20260101-120000"})
            self.assertEqual(build.public_photo_ids(root / "build"), set())

    def test_public_build_skips_private_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "build"
            private = root / "build-private" / "photos" / "20260101-120000"
            public.mkdir()
            private.mkdir(parents=True)
            source = root / "source"
            source.mkdir()
            for name in ("SV_20260101_120000.jpg", "SV_20260101_120001.jpg"):
                (source / name).write_bytes(b"")
            skip = build.private_photo_ids(public)
            self.assertEqual(skip, {"20260101-120000"})
            rows = build.jobs(source, public / "photos", None, skip=skip)
            self.assertEqual([row[1].name for row in rows], ["20260101-120001"])

    def test_reconcile_duplicates_keeps_the_opposite_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "build"
            private = root / "build-private"
            for base in (public, private):
                (base / "photos" / "20260101-120000").mkdir(parents=True)
            (public / "photos" / "20260101-120001").mkdir()
            self.assertEqual(build.reconcile_duplicates(public), {"20260101-120000"})
            self.assertFalse((public / "photos" / "20260101-120000").exists())
            self.assertTrue((private / "photos" / "20260101-120000").exists())
            self.assertTrue((public / "photos" / "20260101-120001").exists())
            (public / "photos" / "20260101-120000").mkdir()
            self.assertEqual(build.reconcile_duplicates(private), {"20260101-120000"})
            self.assertFalse((private / "photos" / "20260101-120000").exists())
            self.assertTrue((public / "photos" / "20260101-120000").exists())

    def test_mixed_album_keeps_existing_public_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            shots = Path(tmp) / "shots"
            source.mkdir()
            for name in ("SV_20260101_120000.jpg", "SV_20260101_120001.jpg"):
                (source / name).write_bytes(b"")
            overrides = {"20260101-120000": "mixed", "20260101-120001": "mixed"}
            self.assertEqual(build.jobs(source, shots, None, private={"mixed"},
                                        overrides=overrides), [])
            covers = build.covers_for([{"id": "20260101-120000", "album": "mixed"}], pins={})
            self.assertEqual(covers["mixed"], "20260101-120000")

    def test_non_sbs_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = albums.NON_SBS_FILE
            albums.NON_SBS_FILE = Path(tmp) / "non_sbs.txt"
            try:
                albums.write_non_sbs({"20240101-120001", "20240101-120000"})
                self.assertEqual(albums.read_non_sbs(),
                                 {"20240101-120000", "20240101-120001"})
                self.assertTrue(albums.NON_SBS_FILE.read_text().startswith("#"))
            finally:
                albums.NON_SBS_FILE = original


if __name__ == "__main__":
    unittest.main()
