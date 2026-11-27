# randompics.de

Source for [randompics.de](https://www.randompics.de/) — a small stereoscopic photo book.
The public page shows depth-mapped SBS (side-by-side) photos: the large view is a 2.5D
WebGL mesh, while gallery thumbnails and album covers use the same depth mesh in a small
hover preview driven by pointer movement. Static covers use the left eye.

This repository is a **code snapshot**. It contains no private photo collections, no
original Valencia source frames, no admin credentials and no share tokens. The two
folders under [`examples/`](examples/) are public photos, included so the rendering and
score pipeline can be inspected without the private archive.

## Layout

- `pipeline/` — build, render, depth, score, publish, share and deploy scripts.
- `admin/` — Flask admin UI (login, one-click visibility, cover picker, bulk album move,
  depth worklist and album tools).
- `web/` — static gallery, shared 2.5D mesh viewer (`viewer.js`) and the small hover previews.
- `tests/` — unit tests for stereo policy, visibility, cover/move routes and grouping.
- `web/covers.json` — public album-to-cover-photo mapping, generated on build/deploy.
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
- Admin credentials, the admin cover pins (`/srv/randompics/covers.json`), `shares.json`,
  `scores.json` and `source/` are intentionally excluded; `web/covers.json` is only the
  public mapping.
- The favicon is `web/favicon.svg`; the small 2.5D previews use the same `RPViewer` mesh as
  the large view and only run while the pointer is over a tile/card.
