# randompics.de

Source for [randompics.de](https://www.randompics.de/) — a small stereoscopic photo book.
The public page shows depth-mapped SBS (side-by-side) photos: the large view is a 2.5D
WebGL mesh, while covers and gallery thumbnails alternate the left/right eye with a CSS
“wiggle” so the small pictures read as stereo too.

This repository is a **code snapshot**. It contains no private photo collections, no
original Valencia source frames, no admin credentials and no share tokens. The two
folders under [`examples/`](examples/) are public photos, included so the rendering and
score pipeline can be inspected without the private archive.

## Layout

- `pipeline/` — build, render, depth, score, publish, share and deploy scripts.
- `admin/` — Flask admin UI (login, visibility, covers, depth worklist, album tools).
- `web/` — static gallery, 2.5D viewer (`viewer.js`) and the stereo wiggle CSS.
- `tests/` — unit tests for stereo policy, visibility and similar-label grouping.
- `examples/` — two public Valencia photos with `scene.jpg`, `sbs.jpg`, `depth.jpg`,
  `thumb.jpg`, `thumb_sbs.jpg` and `meta.json`.

## How a photo is rendered

1. `pipeline/build.py` scans an SBS source frame (positive camera signature, not aspect
   ratio alone), splits the eyes, rectifies the pair and renders a left-eye scene.
2. `pipeline/render.py` writes `scene.jpg`, the full `sbs.jpg`, a small `thumb_sbs.jpg`
   for the gallery wiggle, a grayscale `depth.jpg` and `thumb.jpg`.
3. Depth comes from Depth Anything V2 on a desktop GPU, or SGBM locally as a fallback.
4. `pipeline/deploy.py` publishes only the allowed rows and deletes stale public photos.

## Quality score

`pipeline/score.py` writes a deterministic 0–5 score per photo to `scores.json`. It is a
technical triage signal, **not** an artistic judgement:

- **Sharpness (50%)** — variance of the Laplacian on the 960×720 scene, then a
  percentile rank *within the photo’s album*. High means sharp relative to its album,
  not an absolute sharpness threshold.
- **Exposure (15%)** — `5 × max(0, 1 − clipped_fraction / 0.08)`, where clipped pixels
  are below 6 or above 249. High means almost no blown highlights or crushed blacks.
- **Depth (20%)** — standard deviation of the depth map, percentile-ranked within the
  album. High means a strong near/far separation; a missing depth map scores 0.
- **Duplicate separation (15%)** — nearest-thumbnail normalised cross-correlation:
  5 if similarity < 0.90, 3 if 0.90–<0.95, 1 if ≥ 0.95. High means “not a near-duplicate”.

Composite = `0.50 × sharp + 0.15 × exposure + 0.20 × depth + 0.15 × duplicate`.

A very high score means technically clean, sharp, deep and distinct within its album.
A very low score means at least one of those failed — commonly blur, clipping, a flat or
missing depth map, or a near-duplicate. Because sharpness and depth are album-relative,
a uniformly soft album can still produce high scores; use the score to sort a worklist,
not to reject a photo on its own.

## Notes

- `web/albums.json` contains only the public album labels; the live deployment holds the
  full private data outside Git.
- Admin credentials, `shares.json`, `scores.json`, `covers.json` and `source/` are
  intentionally excluded.
- The favicon is `web/favicon.svg`; the gallery wiggle honours
  `prefers-reduced-motion`.
