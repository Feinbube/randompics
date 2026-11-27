// Gallery + 2.5D viewer for randompics.de. The mesh itself lives in viewer.js
// (shared with the admin review preview); this file owns routing and UI.

// Album labels live in albums.json (shared with the admin UI). A private share
// injects window.RP_ALBUM before this file loads and overrides its album.
let ALBUM_META = {};
let COVER_IDS = {};
const ALBUM_LABELS = { all: "Alle" };

async function loadCoverIds() {
  try {
    const response = await fetch("covers.json", { cache: "no-cache" });
    if (response.ok) COVER_IDS = await response.json();
  } catch (error) {
    COVER_IDS = {};
  }
}

async function loadAlbumMeta() {
  try {
    const response = await fetch("albums.json", { cache: "no-cache" });
    if (response.ok) ALBUM_META = await response.json();
  } catch (error) {
    ALBUM_META = {};
  }
  if (window.RP_ALBUM) {
    ALBUM_META[window.RP_ALBUM.slug] = {
      label: window.RP_ALBUM.label,
      sub: window.RP_ALBUM.sub || "",
      url: window.RP_ALBUM.url || "",
    };
  }
  for (const [slug, meta] of Object.entries(ALBUM_META)) {
    ALBUM_LABELS[slug] = meta.label;
  }
}

const ui = {
  grid: document.getElementById("grid"),
  albums: document.getElementById("albums"),
  albumsView: document.getElementById("albums-view"),
  galleryView: document.getElementById("gallery-view"),
  albumCards: document.getElementById("album-cards"),
  albumTitle: document.getElementById("album-title"),
  albumSub: document.getElementById("album-sub"),
  viewer: document.getElementById("viewer"),
  canvas: document.getElementById("view"),
  fallback: document.getElementById("fallback"),
  title: document.getElementById("title"),
  source: document.getElementById("source"),
  loading: document.getElementById("loading"),
  strength: document.getElementById("strength"),
  sway: document.getElementById("sway"),
  depth: document.getElementById("depth"),
  sbs: document.getElementById("sbs"),
  full: document.getElementById("full"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  close: document.getElementById("close"),
  sbsview: document.getElementById("sbsview"),
  hint: document.getElementById("hint"),
};

const state = {
  album: "all",
  manifest: [],
  index: -1,
  strength: 1,
  sway: true,
  depthMode: false,
  cache: new Map(),
  photo: null,
  viewer: null,
  preview: null,
  previewViewer: null,
  previewCanvas: null,
  previewToken: 0,
};

const photoUrl = (id, file) => `photos/${id}/${file}`;

function loadImage(src) {
  if (state.cache.has(src)) return state.cache.get(src);
  const promise = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Bild nicht ladbar: ${src}`));
    img.src = src;
  });
  state.cache.set(src, promise);
  return promise;
}

/* ---------- gallery ---------- */

function renderGrid() {
  const frag = document.createDocumentFragment();
  for (const photo of state.manifest) {
    const tile = document.createElement("a");
    tile.className = "tile";
    tile.href = `#${photo.id}`;
    tile.dataset.album = photo.album || "all";
    tile.setAttribute("aria-label", `Foto ${photo.id}`);

    const img = document.createElement("img");
    img.src = photoUrl(photo.id, "thumb.jpg");
    img.alt = "";
    img.loading = "lazy";
    img.width = 480;
    img.height = 360;
    tile.dataset.photo = photo.id;
    tile.dataset.depth = photo.ok ? "1" : "0";
    tile.appendChild(img);

    const badge = document.createElement("span");
    badge.className = photo.ok ? "badge" : "badge plain";
    badge.textContent = photo.ok ? "3D" : "Foto";
    tile.appendChild(badge);

    tile.hidden = state.album !== "all" && tile.dataset.album !== state.album;
    frag.appendChild(tile);
  }
  ui.grid.appendChild(frag);
}

function currentList() {
  return state.album === "all"
    ? state.manifest
    : state.manifest.filter((photo) => photo.album === state.album);
}

function renderAlbums() {
  const counts = { all: state.manifest.length };
  for (const photo of state.manifest) {
    counts[photo.album] = (counts[photo.album] || 0) + 1;
  }
  const frag = document.createDocumentFragment();
  for (const slug of Object.keys(ALBUM_LABELS)) {
    if (!counts[slug]) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.album = slug;
    button.textContent = `${ALBUM_LABELS[slug]} (${counts[slug]})`;
    button.setAttribute("aria-pressed", String(slug === state.album));
    button.addEventListener("click", () => {
      location.hash = slug === "all" ? "" : `album=${slug}`;
      applyAlbum(slug, true);
    });
    frag.appendChild(button);
  }
  ui.albums.replaceChildren(frag);
}

function applyAlbum(slug, scroll = false) {
  state.album = ALBUM_LABELS[slug] ? slug : "all";
  for (const button of ui.albums.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.album === state.album));
  }
  for (const tile of ui.grid.querySelectorAll(".tile")) {
    tile.hidden = state.album !== "all" && tile.dataset.album !== state.album;
  }
  // On phones the filter row sits above the fold after a tap; bring the grid up.
  if (scroll && window.matchMedia("(max-width: 720px)").matches) {
    ui.grid.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderAlbumCards() {
  const counts = {};
  for (const photo of state.manifest) {
    counts[photo.album] = (counts[photo.album] || 0) + 1;
  }
  const frag = document.createDocumentFragment();
  for (const [slug, meta] of Object.entries(ALBUM_META)) {
    if (!counts[slug]) continue;
    const card = document.createElement("a");
    card.className = "album-card";
    card.href = `#album=${slug}`;
    card.dataset.cover = COVER_IDS[slug] || "";
    card.innerHTML = `
      <img class="cover-img" src="covers/${slug}.jpg?v=20261127c" alt="" loading="eager" width="2400" height="800">
      <span class="shade"></span>
      <span class="count">${counts[slug]} Fotos</span>
      <span class="meta"><h2>${meta.label}</h2><p class="sub">${meta.sub}</p></span>`;
    frag.appendChild(card);
  }
  ui.albumCards.replaceChildren(frag);
}

function mountHeroWiggle() {
  const box = document.querySelector(".wiggle[data-photo]");
  if (!box) return;
  const id = box.dataset.photo;
  if (!state.manifest.some((photo) => photo.id === id && photo.ok)) {
    const figure = box.closest(".hero-demo");
    if (figure) figure.remove();
    return;
  }
  const img = new Image();
  img.alt = "";
  img.onerror = () => {
    const figure = box.closest(".hero-demo");
    if (figure) figure.remove();
  };
  img.src = photoUrl(id, "sbs.jpg");
  box.appendChild(img);
}

/* ---------- small 2.5D previews ---------- */

function findPreviewTarget(node) {
  return node.closest(".tile[data-photo], .album-card[data-cover]");
}

function previewPhotoId(target) {
  return target.dataset.photo || target.dataset.cover || "";
}

function hidePreview() {
  state.previewToken += 1;
  state.preview = null;
  if (state.previewCanvas) {
    state.previewCanvas.remove();
    state.previewCanvas.hidden = true;
  }
  for (const node of document.querySelectorAll(".previewing")) {
    node.classList.remove("previewing");
  }
}

function updatePreviewTarget(target, event) {
  if (!state.preview || state.preview.target !== target || !state.preview.viewer) return;
  const rect = target.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
  state.preview.viewer.setTarget(-x);
  state.preview.viewer.notePointer();
}

async function showPreview(target) {
  if (!target || !window.RPViewer) return;
  const id = previewPhotoId(target);
  const photo = state.manifest.find((row) => row.id === id);
  if (!photo || !photo.ok) return;
  if (state.preview && state.preview.target === target && state.preview.id === id) return;

  hidePreview();
  const token = state.previewToken;
  const canvas = state.previewCanvas || document.createElement("canvas");
  state.previewCanvas = canvas;
  canvas.className = "preview-canvas";
  canvas.setAttribute("aria-hidden", "true");
  canvas.hidden = false;
  target.appendChild(canvas);
  target.classList.add("previewing");
  state.preview = { target, id, viewer: null, photo };

  try {
    const [scene, depth] = await Promise.all([
      loadImage(photoUrl(id, "scene.jpg")),
      loadImage(photoUrl(id, "depth.jpg")),
    ]);
    if (state.previewToken !== token || state.preview?.target !== target) return;
    if (!state.previewViewer) state.previewViewer = RPViewer.create(canvas);
    const viewer = state.previewViewer;
    if (!viewer.load(scene, depth, photo.anchor)) {
      hidePreview();
      return;
    }
    viewer.setStrength(1.15);
    viewer.setFit("cover");
    viewer.setSway(false);
    viewer.setTarget(0);
    state.preview.viewer = viewer;
  } catch (error) {
    hidePreview();
  }
}

function wirePreviews() {
  for (const root of [ui.grid, ui.albumCards]) {
    root.addEventListener("pointerover", (event) => {
      const target = findPreviewTarget(event.target);
      if (target) showPreview(target);
    });
    root.addEventListener("pointermove", (event) => {
      const target = findPreviewTarget(event.target);
      if (target) updatePreviewTarget(target, event);
    });
    root.addEventListener("pointerout", (event) => {
      const target = findPreviewTarget(event.target);
      const next = event.relatedTarget;
      if (target && !(next instanceof Node && target.contains(next))) hidePreview();
    });
  }
}

function showAlbums() {
  hidePreview();
  ui.albumsView.hidden = false;
  ui.galleryView.hidden = true;
  closeViewer();
  window.scrollTo(0, 0);
}

function showGallery(slug) {
  hidePreview();
  ui.albumsView.hidden = true;
  ui.galleryView.hidden = false;
  closeViewer();
  const meta = ALBUM_META[slug] || { label: "Alle Fotos", sub: `${state.manifest.length} Fotos` };
  if (meta.url) {
    const link = document.createElement("a");
    link.href = meta.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = meta.label;
    ui.albumTitle.replaceChildren(link);
  } else {
    ui.albumTitle.textContent = meta.label;
  }
  ui.albumSub.textContent = meta.sub;
  applyAlbum(slug);
}

/* ---------- viewer ---------- */

function setPressed(button, on) {
  button.setAttribute("aria-pressed", String(on));
}

function showStatus(message, isError = false) {
  ui.loading.textContent = message;
  ui.loading.classList.toggle("gone", !message);
  ui.loading.classList.toggle("error", isError);
}

async function openPhoto(id) {
  const list = currentList();
  let index = list.findIndex((p) => p.id === id);
  const photo = state.manifest.find((p) => p.id === id);
  if (!photo) return;
  if (index < 0) index = state.manifest.findIndex((p) => p.id === id);
  const total = index >= 0 && list.includes(photo) ? list.length : state.manifest.length;
  state.index = index;
  state.photo = photo;

  ui.viewer.hidden = false;
  document.body.classList.add("locked");
  ui.title.textContent = `Foto ${index + 1} / ${total}`;
  ui.source.textContent = `${photo.id.slice(0, 8)} · ${photo.width || 1920}×${photo.height || 1440}`;
  showStatus("lädt …");

  setPressed(ui.depth, false);
  state.depthMode = false;
  setPressed(ui.sbs, false);
  ui.strength.disabled = !photo.ok;
  ui.depth.disabled = !photo.ok;
  ui.sbs.disabled = Boolean(photo.single);

  if (!photo.ok) {
    ui.canvas.hidden = true;
    ui.fallback.hidden = false;
    ui.fallback.alt = `Foto ${photo.id}`;
    ui.fallback.src = photoUrl(photo.id, "scene.jpg");
    showStatus("");
    return;
  }

  try {
    const [scene, depth] = await Promise.all([
      loadImage(photoUrl(photo.id, "scene.jpg")),
      loadImage(photoUrl(photo.id, "depth.jpg")),
    ]);
    if (state.photo !== photo) return;

    if (!state.viewer) state.viewer = RPViewer.create(ui.canvas);
    if (!state.viewer.load(scene, depth, photo.anchor)) {
      ui.canvas.hidden = true;
      ui.fallback.hidden = false;
      ui.fallback.src = scene.src;
      ui.fallback.alt = `Foto ${photo.id}`;
      showStatus("");
      return;
    }

    state.viewer.setStrength(state.strength);
    state.viewer.setSway(state.sway);
    state.viewer.setDepthMode(false);
    ui.canvas.hidden = false;
    ui.fallback.hidden = true;
    showStatus("");
    preloadNeighbours(index);
  } catch (error) {
    ui.canvas.hidden = true;
    ui.fallback.hidden = false;
    ui.fallback.alt = `Foto ${photo.id}`;
    ui.fallback.src = photoUrl(photo.id, "scene.jpg");
    showStatus(`Fehler: ${error.message}`, true);
  }
}

function preloadNeighbours(index) {
  for (const offset of [1, -1]) {
    const neighbour = state.manifest[index + offset];
    if (neighbour && neighbour.ok) {
      loadImage(photoUrl(neighbour.id, "scene.jpg")).catch(() => {});
    }
  }
}

function closeViewer() {
  ui.viewer.hidden = true;
  document.body.classList.remove("locked");
  state.photo = null;
  state.index = -1;
  if (ui.sbs.getAttribute("aria-pressed") === "true") {
    ui.sbs.click();
  }
}

function step(delta) {
  if (state.index < 0) return;
  let list = currentList();
  let index = list.findIndex((p) => p.id === state.photo.id);
  if (index < 0) {
    list = state.manifest;
    index = list.findIndex((p) => p.id === state.photo.id);
  }
  const next = (index + delta + list.length) % list.length;
  location.hash = list[next].id;
}

function route() {
  const raw = decodeURIComponent(location.hash.slice(1));
  if (!raw) {
    if (window.RP_ALBUM) { showGallery(window.RP_ALBUM.slug); return; }
    showAlbums();
    return;
  }
  if (raw.startsWith("album=")) {
    showGallery(raw.slice(6));
    return;
  }
  const photo = state.manifest.find((p) => p.id === raw);
  if (photo) {
    showGallery(photo.album);
    openPhoto(raw);
  }
}

function toggleSbs() {
  const on = ui.sbs.getAttribute("aria-pressed") !== "true";
  setPressed(ui.sbs, on);
  if (on && state.photo) {
    ui.sbsview.src = photoUrl(state.photo.id, "sbs.jpg");
    ui.sbsview.hidden = false;
  } else {
    ui.sbsview.hidden = true;
  }
}

function wireUi() {
  ui.strength.addEventListener("input", () => {
    state.strength = Number(ui.strength.value) / 100;
    if (state.viewer) state.viewer.setStrength(state.strength);
  });

  ui.sway.addEventListener("click", () => {
    state.sway = !state.sway;
    setPressed(ui.sway, state.sway);
    if (state.viewer) state.viewer.setSway(state.sway);
  });

  ui.depth.addEventListener("click", () => {
    if (!state.photo || !state.photo.ok) return;
    state.depthMode = !state.depthMode;
    setPressed(ui.depth, state.depthMode);
    if (state.viewer) state.viewer.setDepthMode(state.depthMode);
  });

  ui.sbs.addEventListener("click", toggleSbs);

  ui.full.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  });

  ui.prev.addEventListener("click", () => step(-1));
  ui.next.addEventListener("click", () => step(1));
  ui.close.addEventListener("click", () => {
    if (ui.sbs.getAttribute("aria-pressed") === "true") { toggleSbs(); return; }
    location.hash = `album=${state.album}`;
  });

  ui.canvas.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch" && event.buttons === 0) return;
    const x = (event.clientX / window.innerWidth) * 2 - 1;
    if (!state.viewer) return;
    state.viewer.setTarget(-x);
    state.viewer.notePointer();
  });

  ui.canvas.addEventListener("pointerleave", () => {
    if (state.viewer) state.viewer.notePointer(0);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!ui.viewer.hidden) {
        if (ui.sbs.getAttribute("aria-pressed") === "true") { toggleSbs(); return; }
        location.hash = `album=${state.album}`;   // Foto -> Album
      } else if (!ui.galleryView.hidden) {
        location.hash = "";                       // Album -> Übersicht
      }
      return;
    }
    if (ui.viewer.hidden) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      state.sway = false;
      setPressed(ui.sway, false);
      if (state.viewer) { state.viewer.setSway(false); state.viewer.setTarget(-0.7); state.viewer.notePointer(); }
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      state.sway = false;
      setPressed(ui.sway, false);
      if (state.viewer) { state.viewer.setSway(false); state.viewer.setTarget(0.7); state.viewer.notePointer(); }
    } else if (event.key.toLowerCase() === "n") {
      step(1);
    } else if (event.key.toLowerCase() === "p") {
      step(-1);
    }
  });

  window.addEventListener("resize", () => {
    if (state.viewer) state.viewer.resize();
    if (state.previewViewer) state.previewViewer.resize();
  });
  window.addEventListener("hashchange", route);
}

async function init() {
  wireUi();
  wirePreviews();
  await loadAlbumMeta();
  await loadCoverIds();
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    state.sway = false;
    setPressed(ui.sway, false);
  }
  // Touch devices have no hover: the ambient sway is the only depth cue, so start
  // at the slider maximum (desktop keeps the gentler default).
  if (window.matchMedia("(pointer: coarse)").matches) {
    ui.strength.value = ui.strength.max;
    state.strength = Number(ui.strength.max) / 100;
  }
  try {
    const response = await fetch("photos/manifest.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`manifest: HTTP ${response.status}`);
    state.manifest = await response.json();
  } catch (error) {
    showStatus(`Galerie nicht ladbar: ${error.message}`, true);
    return;
  }
  for (const node of document.querySelectorAll("#total, #cta-total")) {
    node.textContent = String(state.manifest.length);
  }
  if (window.RP_ALBUM) {
    document.querySelector(".back").hidden = true;
    document.title = `${window.RP_ALBUM.label} · randompics.de`;
  }
  renderGrid();
  renderAlbums();
  renderAlbumCards();
  mountHeroWiggle();
  route();
  setTimeout(() => ui.hint.classList.add("gone"), 6000);
}

init();
