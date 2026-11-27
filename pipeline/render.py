#!/usr/bin/env python3
"""Render one SBS stereo photo into web assets.

Adapted from the sbs3d prototype (that repo keeps its single-photo pipeline).
Outputs per photo: scene.jpg (left eye), sbs.jpg (pair for glasses),
thumb.jpg, thumb_sbs.jpg (small pair for the gallery wiggle), depth.jpg,
meta.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

MARGIN = 16
# Xreal Beam Pro side-by-side frame: two 4:3 eyes, 7680x2880. This is a
# positive signature; aspect ratio alone also matches ordinary panoramas.
SBS_SIZES = frozenset({(7680, 2880)})


class NotStereo(ValueError):
    """The source is an ordinary photo, not a side-by-side stereo frame."""


def is_sbs_size(width: int, height: int) -> bool:
    return (width, height) in SBS_SIZES


def has_camera_exif(path: Path) -> bool:
    """A Beam Pro SBS frame carries no Make/Model; ordinary camera JPEGs do."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            return bool(exif.get(271) or exif.get(272))
    except (OSError, ValueError):
        return False


def split_sbs(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape[:2]
    if not is_sbs_size(w, h):
        raise NotStereo(f"not a supported SBS frame: {w}x{h}")
    half = w // 2
    return img[:, :half].copy(), img[:, half : 2 * half].copy()


def resize_eye(img: np.ndarray, width: int) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)


def _orb_matches(left: np.ndarray, right: np.ndarray):
    gl = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gr = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(6000)
    kl, dl = orb.detectAndCompute(gl, None)
    kr, dr = orb.detectAndCompute(gr, None)
    if dl is None or dr is None or len(dl) < 20 or len(dr) < 20:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    good = sorted(matcher.match(dl, dr), key=lambda m: m.distance)[:1500]
    pl = np.float32([kl[m.queryIdx].pt for m in good])
    pr = np.float32([kr[m.trainIdx].pt for m in good])
    return pl, pr


def rectify_pair(left: np.ndarray, right: np.ndarray):
    """Warp the right eye onto the left so scanlines match; None when hopeless."""
    matches = _orb_matches(left, right)
    if matches is None:
        return right, None
    pl, pr = matches
    H, mask = cv2.findHomography(pr, pl, cv2.RANSAC, 3.0)
    if H is None or mask is None or mask.sum() < 40:
        return right, None
    warped = cv2.warpPerspective(right, H, (left.shape[1], left.shape[0]), flags=cv2.INTER_LINEAR)
    return warped, H


def disparity_range(left: np.ndarray, right: np.ndarray) -> tuple[int, int]:
    matches = _orb_matches(left, right)
    if matches is None:
        return -32, 128
    delta = matches[0] - matches[1]
    lo = float(np.percentile(delta[:, 0], 0.5)) - 8.0
    hi = float(np.percentile(delta[:, 0], 99.5)) + 8.0
    lo_i = int(np.floor(lo))
    span = int(np.ceil((hi - lo_i) / 16.0) * 16)
    return lo_i, max(32, min(span, 256))


def depth_map(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, dict]:
    gl = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gr = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    lo, span = disparity_range(left, right)

    def sgbm(min_disp: int) -> cv2.StereoSGBM:
        return cv2.StereoSGBM_create(
            minDisparity=min_disp,
            numDisparities=span,
            blockSize=7,
            P1=8 * 3 * 49,
            P2=32 * 3 * 49,
            disp12MaxDiff=1,
            uniquenessRatio=10,
            speckleWindowSize=60,
            speckleRange=2,
            preFilterCap=31,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
        )

    lm = sgbm(lo)
    rm = cv2.ximgproc.createRightMatcher(lm)
    dl = lm.compute(gl, gr).astype(np.float32) / 16.0
    dr = rm.compute(gr, gl).astype(np.float32) / 16.0
    wls = cv2.ximgproc.createDisparityWLSFilter(lm)
    wls.setLambda(6000.0)
    wls.setSigmaColor(1.6)
    disp = wls.filter(dl, gl, disparity_map_right=dr)

    valid = disp > (lo + 0.5)
    if valid.mean() < 0.05:
        raise ValueError("disparity failed: too few valid pixels")
    near, far = np.percentile(disp[valid], [98.0, 2.0])
    if near - far < 1.0:
        raise ValueError("disparity failed: flat depth range")
    norm = np.clip((disp - far) / (near - far), 0.0, 1.0)
    depth = (norm * 255.0).astype(np.uint8)
    depth[~valid] = 0
    holes = (~valid).astype(np.uint8) * 255
    depth = cv2.inpaint(depth, holes, 5, cv2.INPAINT_TELEA)
    depth = cv2.medianBlur(depth, 5)
    depth = cv2.GaussianBlur(depth, (0, 0), 2.6)
    stats = {
        "valid_ratio": round(float(valid.mean()), 4),
        "disparity_near_px": round(float(near), 2),
        "disparity_far_px": round(float(far), 2),
    }
    return depth, stats


def render_single(src: Path, out: Path, scene_width: int = 1920) -> dict:
    """Plain gallery entry for a normal (non-stereo) photo: no 2.5D, no pair."""
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read {src}")
    height, width = img.shape[:2]
    scale = scene_width / width
    scene = cv2.resize(img, (scene_width, int(round(height * scale))), interpolation=cv2.INTER_AREA)

    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "scene.jpg"), scene, [cv2.IMWRITE_JPEG_QUALITY, 86])
    thumb_w = 480
    thumb = cv2.resize(scene, (thumb_w, int(round(scene.shape[0] * thumb_w / scene.shape[1]))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "thumb.jpg"), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    meta = {"width": int(scene.shape[1]), "height": int(scene.shape[0]),
            "depth": False, "single": True}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def render_fallback(src: Path, out: Path, scene_width: int = 1920, sbs_eye_width: int = 1280) -> dict:
    """Plain gallery entry for a pair whose depth failed: no 2.5D, still shown."""
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read {src}")
    try:
        left, right = split_sbs(img)
    except ValueError:          # a normal single photo, not a stereo frame
        return render_single(src, out, scene_width)
    scene = resize_eye(left, scene_width)
    pair = resize_eye(right, scene_width)
    m = MARGIN
    scene = scene[m:-m, m:-m]
    pair = pair[m:-m, m:-m]

    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "scene.jpg"), scene, [cv2.IMWRITE_JPEG_QUALITY, 86])
    sbs = np.hstack([resize_eye(scene, sbs_eye_width), resize_eye(pair, sbs_eye_width)])
    cv2.imwrite(str(out / "sbs.jpg"), sbs, [cv2.IMWRITE_JPEG_QUALITY, 82])
    thumb_w = 480
    thumb = cv2.resize(scene, (thumb_w, int(round(scene.shape[0] * thumb_w / scene.shape[1]))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "thumb.jpg"), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    meta = {"width": int(scene.shape[1]), "height": int(scene.shape[0]), "depth": False}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def _load_depth(path: Path, reference: np.ndarray) -> tuple[np.ndarray, dict]:
    """External (learned) depth map, aligned to the left eye before the margin crop."""
    depth = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if depth is None:
        raise ValueError(f"cannot read depth map {path}")
    if depth.shape[:2] != reference.shape[:2]:
        depth = cv2.resize(depth, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    return depth, {"depth_source": "dav2"}


def render_photo(src: Path, out: Path, scene_width: int = 1920, sbs_eye_width: int = 1280,
                 depth_source: Path | None = None) -> dict:
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read {src}")
    if has_camera_exif(src):
        raise NotStereo(f"camera JPEG with Make/Model: {src.name}")

    left, right = split_sbs(img)
    left = resize_eye(left, scene_width)
    right_full = resize_eye(right, scene_width)
    right_sbs = resize_eye(right, sbs_eye_width)

    warped, homography = rectify_pair(left, right_full)
    if homography is not None:
        matches_before = _orb_matches(left, right_full)
        matches_after = _orb_matches(left, warped)
        if matches_before is None or matches_after is None:
            warped, homography = right_full, None
        else:
            before = float(np.abs((matches_before[0] - matches_before[1])[:, 1]).mean())
            after = float(np.abs((matches_after[0] - matches_after[1])[:, 1]).mean())
            if after > before:
                warped, homography = right_full, None

    depth, stats = depth_map(left, warped) if depth_source is None else _load_depth(depth_source, left)
    m = MARGIN
    scene = left[m:-m, m:-m]
    depth = depth[m:-m, m:-m]
    sbs = np.hstack([resize_eye(scene, sbs_eye_width), resize_eye(warped[m:-m, m:-m], sbs_eye_width)])

    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "scene.jpg"), scene, [cv2.IMWRITE_JPEG_QUALITY, 86])
    cv2.imwrite(str(out / "sbs.jpg"), sbs, [cv2.IMWRITE_JPEG_QUALITY, 82])
    thumb_sbs = cv2.resize(sbs, (960, int(round(sbs.shape[0] * 960 / sbs.shape[1]))),
                           interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "thumb_sbs.jpg"), thumb_sbs, [cv2.IMWRITE_JPEG_QUALITY, 78])
    depth_small = cv2.resize(depth, (depth.shape[1] // 2, depth.shape[0] // 2), interpolation=cv2.INTER_AREA)
    if depth_source is None:
        # Foreground dilation: stereo matching fails on dark, low-texture fronts
        # (hair, black clothing) where the subject meets a bright background, and
        # the mesh then tears the silhouette. A learned depth map does not need it.
        depth_small = cv2.dilate(depth_small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        depth_small = cv2.GaussianBlur(depth_small, (0, 0), 1.5)
    cv2.imwrite(str(out / "depth.jpg"), depth_small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    # Anchor the mesh at the photo's own background instead of mid-grey: with the
    # fixed 0.5 the whole frame drifts (median depth is ~0.27), which reads as shear.
    anchor = float(np.percentile(depth_small, 20)) / 255.0
    thumb_w = 480
    thumb = cv2.resize(scene, (thumb_w, int(round(scene.shape[0] * thumb_w / scene.shape[1]))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "thumb.jpg"), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])

    meta = {
        "width": int(scene.shape[1]),
        "height": int(scene.shape[0]),
        "depth_width": int(depth_small.shape[1]),
        "depth_height": int(depth_small.shape[0]),
        "rectified": homography is not None,
        "anchor": round(anchor, 4),
        **stats,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta

COVERS = {
    "oceanografic": "20261101-121758",
    "ciutat": "20261101-164119",
    "forum": "20261102-153826",
    "abend": "20261102-223636",
}


def write_covers(shots: Path, out: Path, covers: dict[str, str] | None = None,
                 width: int = 1200) -> None:
    """3:2 stereo-pair covers: left and right eye side by side.

    The public card shows one eye at a time with the same CSS wiggle as the hero,
    so the album covers participate in the 3D effect instead of staying flat.
    """
    out.mkdir(parents=True, exist_ok=True)
    covers = COVERS if covers is None else covers

    def eye(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        target_h = int(w * 2 / 3)
        if target_h <= h:
            y0 = (h - target_h) // 2
            img = img[y0:y0 + target_h, :]
        else:
            target_w = int(h * 3 / 2)
            x0 = (w - target_w) // 2
            img = img[:, x0:x0 + target_w]
        return cv2.resize(img, (width, int(width * 2 / 3)), interpolation=cv2.INTER_AREA)

    for slug, pid in covers.items():
        pair = shots / pid / "sbs.jpg"
        scene = shots / pid / "scene.jpg"
        src = pair if pair.exists() else scene
        if not src.exists():
            continue
        img = cv2.imread(str(src))
        if img is None:
            continue
        if pair.exists() and img.shape[1] >= 2:
            half = img.shape[1] // 2
            left, right = eye(img[:, :half]), eye(img[:, half:2 * half])
        else:
            left = right = eye(img)
        cv2.imwrite(str(out / f"{slug}.jpg"), np.hstack([left, right]),
                    [cv2.IMWRITE_JPEG_QUALITY, 86])
