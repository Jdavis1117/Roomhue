/* RoomRoller studio. Color math matches app/color_math.py. */

const LIGHTNESS_PULL = 0.72;
const TEXTURE_KEEP = 0.18;
const SHEEN_AMOUNT = { matte: 0, eggshell: 0.035, satin: 0.08, "semi-gloss": 0.15 };
const TINTS = ["#e07a3d", "#2f6f62", "#3d5e8c", "#8c4d6a", "#a6843d", "#4f6b52"];
const D65 = [0.95047, 1, 1.08883];
const DELTA = 6 / 29;
const RECENT_KEY = "roomhue-recent";
const PANEL_KEY = "roomhue-panel-width";
const CONSENT_KEY = "roomroller-consent";
const GOOGLE_SCRIPT = "https://accounts.google.com/gsi/client";

const state = {
  sessionId: null,
  width: 0,
  height: 0,
  tool: "select",
  brushSize: 24,
  brushShape: "circle",
  zoom: 1,
  panX: 0,
  panY: 0,
  spaceDown: false,
  panning: false,
  panOrigin: null,
  zoomAnchor: null,
  hue: 18,
  sat: 0.81,
  val: 0.77,
  pickTarget: null,
  collectionId: null,
  user: null,
  clientId: "",
  legalVersion: "",
  googleReady: null,
  recentMemory: [],
  afterSignIn: null,
  collectionName: "",
  tolerance: 16,
  compare: 1,
  surfaces: [],
  selectedId: null,
  brand: "sherwin-williams",
  search: "",
  colors: [],
  brands: [],
  colorTotal: 0,
  colorNote: "",
  colorRequest: 0,
  colorLoading: false,
  openGroups: {},
  draft: null,
  undo: [],
  redo: [],
  lab: null,
  baseCanvas: null,
  paintCanvas: null,
  paintDirty: true,
  painting: false,
  lastPoint: null,
  customCount: 0,
  maskSerial: 0,
  paintImage: null,
  paintKeys: new Map(),
  tintCache: null,
  frameQueued: false,
  redrawQueued: false,
  fitQueued: false,
  pointers: new Map(),
  gesture: null,
  gestureTarget: null,
  afterGesture: false,
  tap: null,
  lastTap: null,
  lastPointerType: "mouse",
  sheet: "half",
  editing: false,
  mobileGroup: "",
  sheetTab: "fold-brands",
};

const $ = (id) => document.getElementById(id);
const mobileQuery = window.matchMedia("(max-width: 900px)");
const touchQuery = window.matchMedia("(pointer: coarse)");
const view = $("view");
const viewCtx = view.getContext("2d");

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(c) {
  const value = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.max(c, 0) ** (1 / 2.4) - 0.055;
  return Math.min(1, Math.max(0, value));
}

function labF(t) {
  return t > DELTA ** 3 ? Math.cbrt(t) : t / (3 * DELTA * DELTA) + 4 / 29;
}

function labFInv(t) {
  return t > DELTA ? t ** 3 : 3 * DELTA * DELTA * (t - 4 / 29);
}

function rgbToLab(r, g, b) {
  const R = srgbToLinear(r / 255);
  const G = srgbToLinear(g / 255);
  const B = srgbToLinear(b / 255);
  const X = (R * 0.4124564 + G * 0.3575761 + B * 0.1804375) / D65[0];
  const Y = (R * 0.2126729 + G * 0.7151522 + B * 0.072175) / D65[1];
  const Z = (R * 0.0193339 + G * 0.119192 + B * 0.9503041) / D65[2];
  const fx = labF(X);
  const fy = labF(Y);
  const fz = labF(Z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function labToRgb(L, A, B) {
  const fy = (L + 16) / 116;
  const fx = fy + A / 500;
  const fz = fy - B / 200;
  const X = labFInv(fx) * D65[0];
  const Y = labFInv(fy) * D65[1];
  const Z = labFInv(fz) * D65[2];
  const r = linearToSrgb(X * 3.2404542 + Y * -1.5371385 + Z * -0.4985314);
  const g = linearToSrgb(X * -0.969266 + Y * 1.8760108 + Z * 0.041556);
  const b = linearToSrgb(X * 0.0556434 + Y * -0.2040259 + Z * 1.0572252);
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

function parseHex(hex) {
  const value = hex.trim().replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return null;
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function paintLab(hex, shade) {
  const rgb = parseHex(hex);
  if (!rgb) return null;
  const lab = rgbToLab(rgb[0], rgb[1], rgb[2]);
  lab[0] = Math.min(100, Math.max(0, lab[0] + shade));
  return lab;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2800);
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.detail || "Something went wrong.";
  } catch {
    return "Something went wrong.";
  }
}

function setBusy(on) {
  $("busy").hidden = !on;
}

function selected() {
  return state.surfaces.find((surface) => surface.id === state.selectedId) || null;
}

function cloneCanvas(source) {
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  canvas.getContext("2d").drawImage(source, 0, 0);
  return canvas;
}

function emptyMask(width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Could not read that image."));
    image.src = src;
  });
}

async function maskFromPng(b64) {
  const image = await loadImage(`data:image/png;base64,${b64}`);
  const canvas = document.createElement("canvas");
  canvas.width = state.width;
  canvas.height = state.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0, state.width, state.height);
  const pixels = ctx.getImageData(0, 0, state.width, state.height);
  const data = pixels.data;
  for (let i = 0; i < data.length; i += 4) {
    const on = data[i] > 128;
    data[i] = 255;
    data[i + 1] = 255;
    data[i + 2] = 255;
    data[i + 3] = on ? 255 : 0;
  }
  ctx.putImageData(pixels, 0, 0);
  return canvas;
}

function maskAlpha(canvas) {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
}

function sampleMask(canvas, x, y) {
  const ix = Math.round(x);
  const iy = Math.round(y);
  if (ix < 0 || iy < 0 || ix >= canvas.width || iy >= canvas.height) return false;
  const pixel = canvas.getContext("2d", { willReadFrequently: true }).getImageData(ix, iy, 1, 1).data;
  return pixel[3] > 128;
}

function consent() {
  try {
    const value = JSON.parse(localStorage.getItem(CONSENT_KEY) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

function preferencesAllowed() {
  return Boolean(consent()?.preferences);
}

function setConsent(preferences) {
  localStorage.setItem(CONSENT_KEY, JSON.stringify({ preferences, at: new Date().toISOString() }));
  if (!preferences) {
    state.recentMemory = loadRecent();
    localStorage.removeItem(RECENT_KEY);
    localStorage.removeItem(PANEL_KEY);
  } else if (state.recentMemory.length) {
    localStorage.setItem(RECENT_KEY, JSON.stringify(state.recentMemory));
  }
  $("consent").hidden = true;
}

function showConsent() {
  $("consent").hidden = false;
  $("consent-all").focus();
}

function rememberColor(hex) {
  const next = [hex.toUpperCase(), ...loadRecent().filter((item) => item !== hex.toUpperCase())].slice(0, 8);
  state.recentMemory = next;
  if (preferencesAllowed()) localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  renderRecent();
}

function loadRecent() {
  if (!preferencesAllowed()) return state.recentMemory;
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function pushUndo() {
  state.undo.push(snapshot());
  if (state.undo.length > 25) state.undo.shift();
  state.redo = [];
  syncHistoryButtons();
}

function snapshot() {
  return {
    selectedId: state.selectedId,
    surfaces: state.surfaces.map((surface) => ({
      id: surface.id,
      name: surface.name,
      kind: surface.kind,
      confidence: surface.confidence,
      color: surface.color,
      colorLabel: surface.colorLabel || "",
      sheen: surface.sheen,
      coverage: surface.coverage,
      shade: surface.shade,
      included: surface.included !== false,
      maskCanvas: cloneCanvas(surface.maskCanvas),
      maskVersion: surface.maskVersion,
    })),
  };
}

function restore(shot) {
  state.selectedId = shot.selectedId;
  state.surfaces = shot.surfaces.map((surface) => ({ ...surface, maskVersion: freshVersion(), feather: null }));
  state.paintDirty = true;
  renderSurfaces();
  syncFinish();
  redraw();
  syncHistoryButtons();
}

function undo() {
  const shot = state.undo.pop();
  if (!shot) return;
  state.redo.push(snapshot());
  restore(shot);
}

function redo() {
  const shot = state.redo.pop();
  if (!shot) return;
  state.undo.push(snapshot());
  restore(shot);
}

function syncHistoryButtons() {
  $("undo-btn").disabled = state.undo.length === 0;
  $("redo-btn").disabled = state.redo.length === 0;
  $("m-undo").disabled = state.undo.length === 0;
  $("m-redo").disabled = state.redo.length === 0;
}

function freshVersion() {
  state.maskSerial += 1;
  return state.maskSerial;
}

function bumpMask(surface) {
  surface.maskVersion = freshVersion();
  surface.feather = null;
  state.paintDirty = true;
}

function subtractOthers(maskCanvas, exceptId) {
  const ctx = maskCanvas.getContext("2d");
  ctx.globalCompositeOperation = "destination-out";
  for (const surface of state.surfaces) {
    if (surface.id === exceptId) continue;
    ctx.drawImage(surface.maskCanvas, 0, 0);
  }
  ctx.globalCompositeOperation = "source-over";
}

async function adoptSession(payload, keepPhoto) {
  state.sessionId = payload.session_id || state.sessionId;
  if (!keepPhoto) {
    state.width = payload.width;
    state.height = payload.height;
    const image = await loadImage(`data:image/png;base64,${payload.image_png_base64}`);
    state.baseCanvas = document.createElement("canvas");
    state.baseCanvas.width = state.width;
    state.baseCanvas.height = state.height;
    state.baseCanvas.getContext("2d").drawImage(image, 0, 0);
    state.paintCanvas = document.createElement("canvas");
    state.paintCanvas.width = state.width;
    state.paintCanvas.height = state.height;
    view.width = state.width;
    view.height = state.height;
    state.paintImage = null;
    state.paintKeys = new Map();
    state.tintCache = null;
    buildLab();
  }
  state.surfaces = [];
  for (const item of payload.surfaces) {
    state.surfaces.push(await makeSurface(item));
  }
  state.selectedId = state.surfaces[0] ? state.surfaces[0].id : null;
  if (!keepPhoto) {
    state.undo = [];
    state.redo = [];
    state.draft = null;
    state.zoom = 1;
    state.panX = 0;
    state.panY = 0;
    state.collectionId = null;
    state.collectionName = "";
  }
  state.paintDirty = true;
  $("empty").hidden = true;
  document.body.classList.add("has-photo");
  $("studio").hidden = false;
  $("export-btn").disabled = false;
  $("save-btn").disabled = false;
  $("add-surface").disabled = false;
  fit();
  requestAnimationFrame(fit);
  renderSurfaces();
  syncFinish();
  syncHistoryButtons();
  redraw();
  warmSurfaces();
  if (!keepPhoto) setTool(payload.surfaces.length ? "select" : "dots");
  if (!keepPhoto && isMobile() && payload.surfaces.length) toast("Tap a wall, then pick a color below.");
  else setStatus();
}

async function makeSurface(item) {
  return {
    id: item.id,
    name: item.name,
    kind: item.kind,
    confidence: item.confidence || 0,
    color: item.color || null,
    colorLabel: item.color_label || item.colorLabel || "",
    sheen: item.sheen || "eggshell",
    coverage: item.coverage == null ? 0.92 : item.coverage,
    shade: item.shade || 0,
    included: item.included !== false,
    maskCanvas: await maskFromPng(item.mask_png_base64),
    maskVersion: freshVersion(),
    feather: null,
  };
}

function buildLab() {
  const ctx = state.baseCanvas.getContext("2d", { willReadFrequently: true });
  const pixels = ctx.getImageData(0, 0, state.width, state.height);
  const count = state.width * state.height;
  const L = new Float32Array(count);
  const A = new Float32Array(count);
  const B = new Float32Array(count);
  const data = pixels.data;
  for (let i = 0, p = 0; i < count; i += 1, p += 4) {
    const lab = rgbToLab(data[p], data[p + 1], data[p + 2]);
    L[i] = lab[0];
    A[i] = lab[1];
    B[i] = lab[2];
  }
  state.lab = { L, A, B, rgb: new Uint8ClampedArray(data) };
}

function medianOf(values) {
  if (!values.length) return 0;
  values.sort((a, b) => a - b);
  const mid = values.length >> 1;
  return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
}

function surfaceMedian(surface) {
  if (surface.median && surface.medianVersion === surface.maskVersion) return surface.median;
  const alpha = maskAlpha(surface.maskCanvas);
  const count = state.width * state.height;
  const ls = [];
  const as = [];
  const bs = [];
  for (let i = 0, p = 3; i < count; i += 4, p += 16) {
    if (alpha[p] < 128) continue;
    ls.push(state.lab.L[i]);
    as.push(state.lab.A[i]);
    bs.push(state.lab.B[i]);
  }
  surface.median = { L: medianOf(ls), A: medianOf(as), B: medianOf(bs) };
  surface.medianVersion = surface.maskVersion;
  return surface.median;
}

function feather(surface) {
  if (surface.feather && surface.featherVersion === surface.maskVersion) return surface.feather;
  const canvas = document.createElement("canvas");
  canvas.width = state.width;
  canvas.height = state.height;
  const ctx = canvas.getContext("2d");
  ctx.filter = "blur(1.4px)";
  ctx.drawImage(surface.maskCanvas, 0, 0);
  const pixels = ctx.getImageData(0, 0, state.width, state.height).data;
  // Interior pixels stay opaque. The blur only feathers outside the mask,
  // so the seam between two walls is not a soft mix back to the photo.
  const hard = maskAlpha(surface.maskCanvas);
  const alpha = new Float32Array(state.width * state.height);
  let x0 = state.width;
  let y0 = state.height;
  let x1 = 0;
  let y1 = 0;
  for (let i = 0; i < alpha.length; i += 1) {
    const solid = hard[i * 4 + 3] >= 128;
    const soft = pixels[i * 4 + 3] / 255;
    const value = solid ? 1 : soft;
    alpha[i] = value < 0.04 ? 0 : value;
    if (alpha[i]) {
      const x = i % state.width;
      const y = (i - x) / state.width;
      if (x < x0) x0 = x;
      if (x >= x1) x1 = x + 1;
      if (y < y0) y0 = y;
      if (y >= y1) y1 = y + 1;
    }
  }
  surface.feather = alpha;
  surface.box = x1 > x0 ? { x0, y0, x1, y1 } : null;
  surface.featherVersion = surface.maskVersion;
  return alpha;
}

function applySurface(data, surface, region) {
  const paint = paintLab(surface.color, surface.shade);
  if (!paint) return;
  const alpha = feather(surface);
  const box = surface.box;
  if (!box) return;
  const x0 = Math.max(box.x0, region.x0);
  const x1 = Math.min(box.x1, region.x1);
  const y0 = Math.max(box.y0, region.y0);
  const y1 = Math.min(box.y1, region.y1);
  if (x0 >= x1 || y0 >= y1) return;
  const med = surfaceMedian(surface);
  const shine = SHEEN_AMOUNT[surface.sheen] || 0;
  for (let y = y0; y < y1; y += 1) for (let i = y * state.width + x0, end = y * state.width + x1; i < end; i += 1) {
    const coverage = alpha[i] * surface.coverage;
    if (coverage <= 0) continue;
    const L = state.lab.L[i];
    const A = state.lab.A[i];
    const B = state.lab.B[i];
    const L2 = Math.min(100, Math.max(0, L + (paint[0] - med.L) * LIGHTNESS_PULL));
    const A2 = paint[1] + (A - med.A) * TEXTURE_KEEP;
    const B2 = paint[2] + (B - med.B) * TEXTURE_KEEP;
    let [r, g, b] = labToRgb(L2, A2, B2);
    if (shine) {
      const highlight = Math.min(2, Math.max(0, (L - med.L) / 28));
      const spec = highlight * highlight * shine;
      r += (255 - r) * spec;
      g += (255 - g) * spec;
      b += (255 - b) * spec;
    }
    const p = i * 4;
    // Blend over paint already placed by another surface. Blending each wall
    // against the photo lets the original color show through the seam.
    const keep = 1 - coverage;
    data[p] = data[p] * keep + r * coverage;
    data[p + 1] = data[p + 1] * keep + g * coverage;
    data[p + 2] = data[p + 2] * keep + b * coverage;
    data[p + 3] = 255;
  }
}

function warmSurfaces() {
  // Prepare each surface's soft edge and median while the phone is idle, so the first color tap is quick.
  const idle = window.requestIdleCallback || ((run) => setTimeout(run, 60));
  const queue = state.surfaces.slice();
  const next = () => {
    const surface = queue.shift();
    if (!surface) return;
    if (state.surfaces.includes(surface) && state.lab) {
      feather(surface);
      surfaceMedian(surface);
    }
    idle(next);
  };
  idle(next);
}

function paintKey(surface) {
  if (surface.included === false || !surface.color) return "";
  return `${surface.maskVersion}|${surface.color}|${surface.shade}|${surface.sheen}|${surface.coverage}`;
}

function rebuildPaint() {
  // Repaint only the area of surfaces whose mask or paint changed since the last pass.
  const width = state.width;
  const height = state.height;
  const ctx = state.paintCanvas.getContext("2d");
  const painted = new Map();
  for (const surface of state.surfaces) {
    const key = paintKey(surface);
    if (key) painted.set(surface.id, { key, surface });
  }
  let region = null;
  const grow = (box) => {
    if (!box) return;
    region = region
      ? { x0: Math.min(region.x0, box.x0), y0: Math.min(region.y0, box.y0), x1: Math.max(region.x1, box.x1), y1: Math.max(region.y1, box.y1) }
      : { ...box };
  };
  if (!state.paintImage || state.paintImage.width !== width || state.paintImage.height !== height) {
    state.paintImage = ctx.createImageData(width, height);
    region = { x0: 0, y0: 0, x1: width, y1: height };
  } else {
    for (const [id, before] of state.paintKeys) {
      const now = painted.get(id);
      if (!now || now.key !== before.key) grow(before.box);
    }
    for (const [id, now] of painted) {
      const before = state.paintKeys.get(id);
      if (before && before.key === now.key) continue;
      feather(now.surface);
      grow(now.surface.box);
    }
  }
  if (region) {
    const data = state.paintImage.data;
    const src = state.lab.rgb;
    for (let y = region.y0; y < region.y1; y += 1) {
      const start = (y * width + region.x0) * 4;
      data.set(src.subarray(start, (y * width + region.x1) * 4), start);
    }
    for (const { surface } of painted.values()) applySurface(data, surface, region);
    ctx.putImageData(state.paintImage, 0, 0, region.x0, region.y0, region.x1 - region.x0, region.y1 - region.y0);
  }
  state.paintKeys = new Map();
  for (const [id, { key, surface }] of painted) {
    feather(surface);
    state.paintKeys.set(id, { key, box: surface.box });
  }
  state.paintDirty = false;
}

function tintLayer(skipId) {
  const key = state.surfaces
    .map((surface, index) =>
      surface.included === false || surface.color || surface.id === skipId
        ? ""
        : `${surface.id}:${surface.maskVersion}:${index}:${surface.id === state.selectedId ? 1 : 0}`,
    )
    .join(",");
  let cache = state.tintCache;
  if (cache && cache.key === key && cache.canvas.width === state.width && cache.canvas.height === state.height) return cache.canvas;
  if (!cache) cache = state.tintCache = { canvas: document.createElement("canvas"), key: "" };
  cache.canvas.width = state.width;
  cache.canvas.height = state.height;
  const ctx = cache.canvas.getContext("2d");
  state.surfaces.forEach((surface, index) => {
    if (surface.included === false || surface.color || surface.id === skipId) return;
    drawTint(ctx, surface, TINTS[index % TINTS.length], surface.id === state.selectedId ? 0.42 : 0.28);
  });
  cache.key = key;
  return cache.canvas;
}

function requestFrame() {
  if (state.frameQueued) return;
  state.frameQueued = true;
  requestAnimationFrame(runFrame);
}

function runFrame() {
  state.frameQueued = false;
  if (state.gesture && state.gestureTarget) {
    const target = state.gestureTarget;
    state.gestureTarget = null;
    placeAt((state.gesture.zoom * target.dist) / state.gesture.dist, state.gesture.fraction, target);
  } else if (state.fitQueued) {
    fit();
  }
  state.fitQueued = false;
  if (state.redrawQueued) {
    state.redrawQueued = false;
    redraw();
  }
}

function scheduleRedraw() {
  state.redrawQueued = true;
  requestFrame();
}

function scheduleFit() {
  state.fitQueued = true;
  requestFrame();
}

function drawTint(ctx, surface, color, alpha) {
  const scratch = drawTint.canvas || (drawTint.canvas = document.createElement("canvas"));
  if (scratch.width !== state.width) {
    scratch.width = state.width;
    scratch.height = state.height;
  }
  const g = scratch.getContext("2d");
  g.clearRect(0, 0, state.width, state.height);
  g.globalCompositeOperation = "source-over";
  g.drawImage(surface.maskCanvas, 0, 0);
  g.globalCompositeOperation = "source-in";
  g.fillStyle = color;
  g.fillRect(0, 0, state.width, state.height);
  g.globalCompositeOperation = "source-over";
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.drawImage(scratch, 0, 0);
  ctx.restore();
}

function redraw() {
  if (!state.baseCanvas) return;
  if (state.paintDirty && !state.painting) rebuildPaint();
  viewCtx.clearRect(0, 0, state.width, state.height);
  viewCtx.drawImage(state.paintCanvas, 0, 0);
  const live = state.painting ? selected() : null;
  viewCtx.drawImage(tintLayer(live ? live.id : null), 0, 0);
  if (live && live.included !== false && !live.color) {
    const index = state.surfaces.indexOf(live);
    drawTint(viewCtx, live, TINTS[index % TINTS.length], 0.42);
  }
  if (state.compare < 0.999) {
    const cut = state.width * (1 - state.compare);
    viewCtx.save();
    viewCtx.beginPath();
    viewCtx.rect(0, 0, cut, state.height);
    viewCtx.clip();
    viewCtx.drawImage(state.baseCanvas, 0, 0);
    viewCtx.restore();
    viewCtx.fillStyle = "#fffcf8";
    viewCtx.fillRect(Math.round(cut), 0, 2, state.height);
  }
  if (state.painting) {
    const active = selected();
    if (active && active.included !== false) drawTint(viewCtx, active, "#fffaf4", 0.35);
  }
  drawGuides();
}

function drawGuides() {
  const line = state.draft;
  if (!line || !line.length) return;
  const radius = screenRadius(7);
  viewCtx.save();
  viewCtx.lineJoin = "round";
  viewCtx.lineCap = "round";
  viewCtx.beginPath();
  viewCtx.moveTo(line[0].x, line[0].y);
  for (let i = 1; i < line.length; i += 1) viewCtx.lineTo(line[i].x, line[i].y);
  viewCtx.lineWidth = screenRadius(3);
  viewCtx.strokeStyle = "rgba(255, 252, 248, 0.92)";
  viewCtx.stroke();
  viewCtx.lineWidth = screenRadius(1.5);
  viewCtx.strokeStyle = "#c45c26";
  viewCtx.stroke();
  if (line.length >= 3) {
    viewCtx.setLineDash([screenRadius(6), screenRadius(5)]);
    viewCtx.beginPath();
    viewCtx.moveTo(line[line.length - 1].x, line[line.length - 1].y);
    viewCtx.lineTo(line[0].x, line[0].y);
    viewCtx.stroke();
  }
  for (const point of line) {
    viewCtx.setLineDash([]);
    viewCtx.beginPath();
    viewCtx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    viewCtx.fillStyle = "#fffcf8";
    viewCtx.fill();
    viewCtx.beginPath();
    viewCtx.arc(point.x, point.y, radius * 0.62, 0, Math.PI * 2);
    viewCtx.fillStyle = "#c45c26";
    viewCtx.fill();
  }
  viewCtx.restore();
}

function screenRadius(pixels) {
  const rect = view.getBoundingClientRect();
  const scale = rect.width / view.width;
  return scale ? pixels / scale : pixels;
}

function baseSize() {
  const stage = $("stage");
  const base = Math.min(stage.clientWidth / state.width, stage.clientHeight / state.height);
  return {
    stage,
    width: Math.max(1, Math.floor(state.width * base)),
    height: Math.max(1, Math.floor(state.height * base)),
  };
}

function fit() {
  if (!state.width) return;
  clampPan();
  const { stage, width, height } = baseSize();
  if (view.style.width !== `${width}px`) view.style.width = `${width}px`;
  if (view.style.height !== `${height}px`) view.style.height = `${height}px`;
  const x = (stage.clientWidth - width * state.zoom) / 2 + state.panX;
  const y = (stage.clientHeight - height * state.zoom) / 2 + state.panY;
  view.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${state.zoom})`;
}

function clampPan() {
  const { stage, width, height } = baseSize();
  const maxX = Math.max(0, (width * state.zoom - stage.clientWidth) / 2);
  const maxY = Math.max(0, (height * state.zoom - stage.clientHeight) / 2);
  state.panX = Math.min(maxX, Math.max(-maxX, state.panX));
  state.panY = Math.min(maxY, Math.max(-maxY, state.panY));
}

function fractionAt(point) {
  const rect = view.getBoundingClientRect();
  if (!rect.width || !rect.height) return { x: 0.5, y: 0.5 };
  return { x: (point.x - rect.left) / rect.width, y: (point.y - rect.top) / rect.height };
}

function placeAt(zoom, fraction, point) {
  // Zoom so the spot at `fraction` of the photo sits under the screen `point`.
  state.zoom = Math.min(8, Math.max(1, zoom));
  const { stage, width, height } = baseSize();
  const box = stage.getBoundingClientRect();
  const shownWidth = width * state.zoom;
  const shownHeight = height * state.zoom;
  state.panX = point.x - box.left - (stage.clientWidth - shownWidth) / 2 - fraction.x * shownWidth;
  state.panY = point.y - box.top - (stage.clientHeight - shownHeight) / 2 - fraction.y * shownHeight;
  fit();
}

function setZoom(next, anchor) {
  if (!state.width) return;
  const box = $("stage").getBoundingClientRect();
  const point = anchor || { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  placeAt(next, fractionAt(point), point);
}

function zoomAnchor() {
  if (state.zoomAnchor) return state.zoomAnchor;
  const stage = $("stage").getBoundingClientRect();
  return { x: stage.left + stage.width / 2, y: stage.top + stage.height / 2 };
}

function setStatus() {
  if (state.tool === "dots") {
    const count = state.draft ? state.draft.length : 0;
    $("status").textContent = count
      ? `${count} dot${count === 1 ? "" : "s"} placed. ${tapWord()} the remaining corners, then Add wall.`
      : `${tapWord()} to place a dot at each corner of a wall.`;
    return;
  }
  if (!state.surfaces.length) {
    $("status").textContent = "No walls were marked. Use Dots to place a corner of each wall.";
    return;
  }
  const walls = state.surfaces.filter((surface) => surface.kind === "wall").length;
  const extras = state.surfaces
    .filter((surface) => surface.kind !== "wall")
    .map((surface) => surface.name.toLowerCase());
  const tools = touchQuery.matches
    ? {
        select: "Tap a wall, then pick a color. Pinch or double-tap to zoom.",
        wand: "Tap a wall to mark it. Raise wand reach if it stops short.",
        brush: "Paint with a finger to add to the surface. Two fingers zoom.",
        eraser: "Erase with a finger where the color should not go.",
      }
    : {
        select: "Click a tinted surface, then pick a color. Click it again to deselect. Uncheck one to leave it out.",
        wand: "Click a wall to mark it. Raise wand reach if the selection stops short.",
        brush: "Paint the mask to add the missing part of a surface.",
        eraser: "Erase the mask where the color should not go.",
      };
  const found = [`${walls} wall${walls === 1 ? "" : "s"}`, ...extras].filter(Boolean).join(", ");
  $("status").textContent = `${found}. ${tools[state.tool]}`;
}

function renderSurfaces() {
  const list = $("surface-list");
  list.innerHTML = "";
  if (!state.surfaces.length) {
    const empty = document.createElement("li");
    empty.textContent = "None yet.";
    list.appendChild(empty);
  }
  state.surfaces.forEach((surface, index) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const on = surface.included !== false;
    button.className = `surface${surface.id === state.selectedId ? " is-selected" : ""}${on ? "" : " is-off"}`;
    const include = document.createElement("input");
    include.type = "checkbox";
    include.checked = on;
    include.title = on ? `Leave ${surface.name} out` : `Use ${surface.name}`;
    include.setAttribute("aria-label", on ? `Leave ${surface.name} out` : `Use ${surface.name}`);
    include.addEventListener("change", () => {
      pushUndo();
      surface.included = include.checked;
      if (!include.checked && state.selectedId === surface.id) state.selectedId = null;
      state.paintDirty = true;
      renderSurfaces();
      syncFinish();
      redraw();
    });
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = surface.color || TINTS[index % TINTS.length];
    const label = document.createElement("span");
    const title = document.createElement("span");
    title.textContent = surface.name;
    const meta = document.createElement("small");
    meta.textContent = `${surface.kind}${surface.confidence ? ` · ${Math.round(surface.confidence * 100)}%` : ""}`;
    label.append(title, meta);
    button.append(chip, label);
    button.addEventListener("click", () => selectSurface(surface.id));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      pushUndo();
      state.surfaces = state.surfaces.filter((item) => item.id !== surface.id);
      if (state.selectedId === surface.id) state.selectedId = state.surfaces[0] ? state.surfaces[0].id : null;
      state.paintDirty = true;
      renderSurfaces();
      syncFinish();
      redraw();
      setStatus();
    });
    const row = document.createElement("div");
    row.className = "surface-row";
    row.append(include, button, remove);
    item.appendChild(row);
    list.appendChild(item);
  });
  const current = selected();
  $("clear-color").disabled = !current || !current.color;
  $("clear-color-finish").disabled = !current || !current.color;
  $("apply-all").disabled = !current || !current.color;
}

function selectSurface(id) {
  state.selectedId = state.selectedId === id ? null : id;
  renderSurfaces();
  syncFinish();
  updateSwatchSelection();
  redraw();
}

function syncFinish() {
  const current = selected();
  const sheen = current ? current.sheen : "eggshell";
  document.querySelectorAll("[data-sheen]").forEach((button) => {
    button.classList.toggle("is-on", button.dataset.sheen === sheen);
    button.disabled = !current;
  });
  $("coverage").disabled = !current;
  $("shade").disabled = !current;
  $("coverage").value = current ? Math.round(current.coverage * 100) : 92;
  $("shade").value = current ? current.shade : 0;
  $("coverage-out").textContent = $("coverage").value;
  $("shade-out").textContent = $("shade").value;
}

function renderBrands() {
  const wrap = $("families");
  wrap.innerHTML = "";
  state.brands.forEach((brand) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `family${brand.id === state.brand ? " is-on" : ""}`;
    button.textContent = brand.name;
    button.addEventListener("click", () => {
      state.brand = state.brand === brand.id ? "" : brand.id;
      loadColors(true);
    });
    wrap.appendChild(button);
  });
  const select = $("brand-select");
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All brands";
  select.appendChild(all);
  state.brands.forEach((brand) => {
    const option = document.createElement("option");
    option.value = brand.id;
    option.textContent = brand.name;
    select.appendChild(option);
  });
  select.value = state.brand;
}

const PAINT_GROUPS = ["Whites", "Grays", "Blacks", "Browns", "Reds", "Oranges", "Yellows", "Greens", "Blues", "Purples", "Pinks"];

function renderSwatches() {
  const wrap = $("swatches");
  const scroll = wrap.scrollTop;
  wrap.innerHTML = "";
  const query = state.search.trim();
  $("color-note").textContent = state.colorNote || "";
  if (!state.brand && query.length < 2) {
    $("color-count").textContent = "Pick a brand or type at least two letters.";
  } else if (!state.colorTotal) {
    $("color-count").textContent = "No matching paint.";
  } else {
    $("color-count").textContent = `${state.colorTotal.toLocaleString()} paints`;
  }
  $("color-more").hidden = true;
  const grouped = new Map();
  state.colors.forEach((color) => {
    const name = color.group || "Other";
    if (!grouped.has(name)) grouped.set(name, []);
    grouped.get(name).push(color);
  });
  const names = PAINT_GROUPS.filter((name) => grouped.has(name));
  grouped.forEach((_colors, name) => {
    if (!names.includes(name)) names.push(name);
  });
  const chips = $("group-chips");
  chips.innerHTML = "";
  if (isMobile()) {
    // Phones show one color family at a time, chosen from a chip row, so the grid gets the room.
    if (!names.includes(state.mobileGroup)) state.mobileGroup = names[0] || "";
    names.forEach((name) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `group-chip${name === state.mobileGroup ? " is-on" : ""}`;
      chip.setAttribute("role", "tab");
      chip.setAttribute("aria-selected", String(name === state.mobileGroup));
      const label = document.createElement("span");
      label.textContent = name;
      const count = document.createElement("small");
      count.textContent = grouped.get(name).length.toLocaleString();
      chip.append(label, count);
      chip.addEventListener("click", () => {
        state.mobileGroup = name;
        renderSwatches();
        $("swatches").scrollTop = 0;
      });
      chips.appendChild(chip);
    });
    const grid = document.createElement("div");
    grid.className = "group-paints";
    const active = selected();
    const fragment = document.createDocumentFragment();
    (grouped.get(state.mobileGroup) || []).forEach((color) => fragment.appendChild(paintButton(color, active)));
    grid.appendChild(fragment);
    wrap.appendChild(grid);
    wrap.scrollTop = scroll;
    const on = chips.querySelector(".is-on");
    if (on) chips.scrollLeft = on.offsetLeft - chips.clientWidth / 2 + on.offsetWidth / 2;
    return;
  }
  names.forEach((name) => {
    const details = document.createElement("details");
    details.className = "color-group";
    details.open = Boolean(state.openGroups[name]);
    const summary = document.createElement("summary");
    const title = document.createElement("span");
    title.textContent = name;
    const count = document.createElement("small");
    count.textContent = String(grouped.get(name).length);
    summary.append(title, count);
    const grid = document.createElement("div");
    grid.className = "group-paints";
    const fill = () => {
      if (grid.childElementCount) return;
      const active = selected();
      const fragment = document.createDocumentFragment();
      grouped.get(name).forEach((color) => fragment.appendChild(paintButton(color, active)));
      grid.appendChild(fragment);
    };
    if (details.open) fill();
    details.append(summary, grid);
    details.addEventListener("toggle", () => {
      state.openGroups[name] = details.open;
      if (details.open) fill();
    });
    wrap.appendChild(details);
  });
  wrap.scrollTop = scroll;
}

function paintButton(color, current) {
  const label = `${color.brand} ${color.code} ${color.name}`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "paint";
  button.dataset.label = label;
  button.classList.toggle("is-on", Boolean(current && current.colorLabel === label));
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.style.background = color.hex;
  const rgb = parseHex(color.hex);
  if (rgb) button.style.setProperty("--on-chip", rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114 > 150 ? "#1c1917" : "#fffcf8");
  const text = document.createElement("span");
  text.className = "paint-label";
  const title = document.createElement("strong");
  title.textContent = color.name;
  const meta = document.createElement("small");
  meta.textContent = color.code;
  text.append(title, meta);
  button.title = `${color.brand} · ${color.code} · ${color.hex}${color.archived ? " · archived" : ""}`;
  button.append(chip, text);
  button.setAttribute("aria-label", `${label} ${color.hex}`);
  button.addEventListener("click", () => chooseColor(color.hex, label));
  return button;
}

function updateSwatchSelection() {
  const current = selected();
  document.querySelectorAll("#swatches .paint.is-on").forEach((button) => button.classList.remove("is-on"));
  if (!current || !current.colorLabel) return;
  const match = document.querySelector(`#swatches .paint[data-label="${CSS.escape(current.colorLabel)}"]`);
  if (match) match.classList.add("is-on");
}

async function loadColors(reset) {
  if (state.colorLoading && !reset) return;
  if (reset) state.openGroups = {};
  const request = (state.colorRequest += 1);
  state.colorLoading = true;
  const offset = reset ? 0 : state.colors.length;
  const params = new URLSearchParams({
    brand: state.brand,
    q: state.search.trim(),
    limit: "20000",
    offset: String(offset),
  });
  try {
    const response = await fetch(`/api/colors?${params}`);
    if (!response.ok) throw new Error(await readError(response));
    const body = await response.json();
    if (request !== state.colorRequest) return;
    state.colors = reset ? body.colors : state.colors.concat(body.colors);
    state.colorTotal = body.total;
    state.brands = body.brands;
    state.colorNote = body.note || "";
    renderBrands();
    renderSwatches();
  } catch (error) {
    if (request !== state.colorRequest) return;
    toast(error.message || "The color list didn't load.");
  } finally {
    if (request === state.colorRequest) state.colorLoading = false;
  }
}

function renderRecent() {
  const wrap = $("recent");
  const colors = loadRecent();
  wrap.innerHTML = "";
  wrap.hidden = colors.length === 0;
  colors.forEach((hex) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "swatch";
    button.style.background = hex;
    button.title = hex;
    button.setAttribute("aria-label", hex);
    button.addEventListener("click", () => chooseColor(hex));
    wrap.appendChild(button);
  });
}

function chooseColor(hex, label) {
  const current = selected();
  if (!current) {
    toast("Select a surface first.");
    return;
  }
  pushUndo();
  current.color = hex.toUpperCase();
  current.colorLabel = label || "";
  state.paintDirty = true;
  rememberColor(current.color);
  renderSurfaces();
  updateSwatchSelection();
  redraw();
  if (isMobile() && label) toast(label);
  setPickerFromHex(current.color);
}

function hsvToRgb(hue, sat, val) {
  const chroma = val * sat;
  const sector = (hue % 360) / 60;
  const x = chroma * (1 - Math.abs((sector % 2) - 1));
  let red = 0;
  let green = 0;
  let blue = 0;
  if (sector < 1) [red, green, blue] = [chroma, x, 0];
  else if (sector < 2) [red, green, blue] = [x, chroma, 0];
  else if (sector < 3) [red, green, blue] = [0, chroma, x];
  else if (sector < 4) [red, green, blue] = [0, x, chroma];
  else if (sector < 5) [red, green, blue] = [x, 0, chroma];
  else [red, green, blue] = [chroma, 0, x];
  const match = val - chroma;
  return [Math.round((red + match) * 255), Math.round((green + match) * 255), Math.round((blue + match) * 255)];
}

function rgbToHsv(red, green, blue) {
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let hue = 0;
  if (delta !== 0) {
    if (max === r) hue = ((g - b) / delta) % 6;
    else if (max === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
    hue *= 60;
    if (hue < 0) hue += 360;
  }
  return [hue, max === 0 ? 0 : delta / max, max];
}

function pickerHex() {
  const [red, green, blue] = hsvToRgb(state.hue, state.sat, state.val);
  const part = (value) => value.toString(16).padStart(2, "0");
  return `#${part(red)}${part(green)}${part(blue)}`.toUpperCase();
}

function setPickerFromHex(hex) {
  const rgb = parseHex(hex);
  if (!rgb) return;
  const [hue, sat, val] = rgbToHsv(rgb[0], rgb[1], rgb[2]);
  if (sat > 0.001) state.hue = hue;
  state.sat = sat;
  state.val = val;
  $("custom-hex").value = hex.toUpperCase();
  drawPicker();
}

function sizePicker() {
  const sv = $("picker-sv");
  const hue = $("picker-hue");
  const width = Math.max(10, Math.floor(sv.clientWidth));
  sv.width = width;
  sv.height = 160;
  hue.width = width;
  hue.height = 18;
  drawPicker();
}

function drawPicker() {
  const sv = $("picker-sv");
  const hueBar = $("picker-hue");
  if (!sv.width || !hueBar.width) return;
  const ctx = sv.getContext("2d");
  const color = hsvToRgb(state.hue, 1, 1);
  const hueCss = `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  const horizontal = ctx.createLinearGradient(0, 0, sv.width, 0);
  horizontal.addColorStop(0, "#ffffff");
  horizontal.addColorStop(1, hueCss);
  ctx.fillStyle = horizontal;
  ctx.fillRect(0, 0, sv.width, sv.height);
  const vertical = ctx.createLinearGradient(0, 0, 0, sv.height);
  vertical.addColorStop(0, "rgba(0, 0, 0, 0)");
  vertical.addColorStop(1, "#000000");
  ctx.fillStyle = vertical;
  ctx.fillRect(0, 0, sv.width, sv.height);
  const markerX = state.sat * sv.width;
  const markerY = (1 - state.val) * sv.height;
  ctx.beginPath();
  ctx.arc(markerX, markerY, 6, 0, Math.PI * 2);
  ctx.strokeStyle = "#fffcf8";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.strokeStyle = "rgba(0, 0, 0, 0.55)";
  ctx.lineWidth = 1;
  ctx.stroke();

  const hueCtx = hueBar.getContext("2d");
  const rainbow = hueCtx.createLinearGradient(0, 0, hueBar.width, 0);
  rainbow.addColorStop(0, "#ff0000");
  rainbow.addColorStop(0.17, "#ffff00");
  rainbow.addColorStop(0.33, "#00ff00");
  rainbow.addColorStop(0.5, "#00ffff");
  rainbow.addColorStop(0.67, "#0000ff");
  rainbow.addColorStop(0.83, "#ff00ff");
  rainbow.addColorStop(1, "#ff0000");
  hueCtx.fillStyle = rainbow;
  hueCtx.fillRect(0, 0, hueBar.width, hueBar.height);
  const hueX = (state.hue / 360) * hueBar.width;
  hueCtx.fillStyle = "#fffcf8";
  hueCtx.fillRect(Math.round(hueX) - 1, 0, 2, hueBar.height);
}

function pickerFraction(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  };
}

function previewPicker() {
  const hex = pickerHex();
  $("custom-hex").value = hex;
  drawPicker();
  const current = selected();
  if (!current) return;
  current.color = hex;
  current.colorLabel = "";
  state.paintDirty = true;
  scheduleRedraw();
}

function beginPick(event, canvas, target) {
  canvas.setPointerCapture(event.pointerId);
  state.pickTarget = target;
  if (selected()) pushUndo();
  movePick(event);
}

function movePick(event) {
  if (state.pickTarget === "sv") {
    const point = pickerFraction(event, $("picker-sv"));
    state.sat = point.x;
    state.val = 1 - point.y;
    previewPicker();
  } else if (state.pickTarget === "hue") {
    const point = pickerFraction(event, $("picker-hue"));
    state.hue = Math.min(359.999, point.x * 360);
    previewPicker();
  }
}

function finishPick() {
  if (!state.pickTarget) return;
  state.pickTarget = null;
  const current = selected();
  if (!current || !current.color) return;
  rememberColor(current.color);
  renderSurfaces();
  updateSwatchSelection();
}

function tapWord() {
  return touchQuery.matches ? "Tap" : "Click";
}

function setTool(tool) {
  state.tool = tool;
  $("studio").dataset.tool = tool;
  if (tool !== "select" && isMobile() && !state.editing) setEditing(true);
  document.querySelectorAll("button[data-tool]").forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.tool === tool ? "true" : "false");
  });
  $("brush-cursor").hidden = tool !== "brush" && tool !== "eraser";
  syncBrushCursor();
  view.style.cursor = state.spaceDown ? "grab" : tool === "select" || tool === "wand" || tool === "dots" ? "crosshair" : "none";
  syncLineButtons();
  setStatus();
  redraw();
}

function syncLineButtons() {
  $("line-actions").hidden = state.tool !== "dots";
  const draft = state.draft ? state.draft.length : 0;
  $("add-wall").disabled = draft < 3;
  $("clear-dots").disabled = draft === 0;
}

function addLinePoint(point) {
  if (!state.draft) state.draft = [];
  const last = state.draft[state.draft.length - 1];
  if (last && Math.hypot(last.x - point.x, last.y - point.y) < screenRadius(8)) return;
  const first = state.draft[0];
  if (first && state.draft.length >= 3 && Math.hypot(first.x - point.x, first.y - point.y) < screenRadius(16)) {
    addWallFromDots();
    return;
  }
  state.draft.push(point);
  redraw();
  syncLineButtons();
  setStatus();
}

function addWallFromDots() {
  if (!state.draft || state.draft.length < 3) {
    toast("Place at least three dots around the wall.");
    return;
  }
  pushUndo();
  const maskCanvas = emptyMask(state.width, state.height);
  const ctx = maskCanvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.moveTo(state.draft[0].x, state.draft[0].y);
  for (let i = 1; i < state.draft.length; i += 1) ctx.lineTo(state.draft[i].x, state.draft[i].y);
  ctx.closePath();
  ctx.fill();
  const wallNumber = state.customCount + 1;
  state.customCount = wallNumber;
  state.surfaces.push({
    id: `d${Date.now()}`,
    name: `Wall ${wallNumber}`,
    kind: "wall",
    confidence: 1,
    color: null,
    sheen: "eggshell",
    coverage: 0.92,
    shade: 0,
    included: true,
    maskCanvas,
    maskVersion: freshVersion(),
    feather: null,
  });
  state.selectedId = state.surfaces[state.surfaces.length - 1].id;
  state.draft = null;
  state.paintDirty = true;
  renderSurfaces();
  syncFinish();
  syncLineButtons();
  redraw();
  setStatus();
}

function undoLinePoint() {
  if (!state.draft || !state.draft.length) return;
  state.draft.pop();
  if (!state.draft.length) state.draft = null;
  redraw();
  syncLineButtons();
  setStatus();
}

function clearLines() {
  state.draft = null;
  redraw();
  syncLineButtons();
  setStatus();
}

function eventPoint(event) {
  const rect = view.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) * view.width) / rect.width,
    y: ((event.clientY - rect.top) * view.height) / rect.height,
  };
}

function hitSurface(x, y) {
  const hits = state.surfaces.filter(
    (surface) => surface.included !== false && sampleMask(surface.maskCanvas, x, y),
  );
  hits.sort((a, b) => maskArea(a) - maskArea(b));
  return hits[0] || null;
}

function maskArea(surface) {
  const alpha = maskAlpha(surface.maskCanvas);
  let count = 0;
  for (let i = 3; i < alpha.length; i += 16) if (alpha[i] > 128) count += 1;
  return count;
}

function isTouchLike(event) {
  return event.pointerType === "touch" || event.pointerType === "pen";
}

function pinchInfo() {
  const [a, b] = [...state.pointers.values()];
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, dist: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
}

function startPinch() {
  cancelStroke();
  state.tap = null;
  state.panning = false;
  const info = pinchInfo();
  state.gesture = { dist: info.dist, zoom: state.zoom, fraction: fractionAt(info) };
}

async function onPointerDown(event) {
  // Listens on the whole stage (capture phase), so a second finger counts even if it lands beside
  // the photo or on a floating button. A first touch on a button is left to the button.
  if (!state.sessionId) return;
  if (event.target.closest("button")) {
    if (!state.pointers.size || !isTouchLike(event)) return;
    event.stopPropagation();
    event.preventDefault();
  }
  state.lastPointerType = event.pointerType;
  if (event.pointerType === "mouse" && event.button !== 0 && event.button !== 1) return;
  $("stage").setPointerCapture(event.pointerId);
  state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (state.pointers.size === 2 && isTouchLike(event)) {
    startPinch();
    return;
  }
  if (state.pointers.size > 1 || state.gesture || state.afterGesture) return;
  if (state.spaceDown || event.button === 1) {
    event.preventDefault();
    state.panning = true;
    state.panOrigin = { x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY };
    view.style.cursor = "grabbing";
    return;
  }
  const point = eventPoint(event);
  if (state.tool === "brush" || state.tool === "eraser") {
    beginStroke(point);
    return;
  }
  if (isTouchLike(event)) {
    // Wait for the finger to lift: a drag pans the photo, a tap acts.
    state.tap = { x: event.clientX, y: event.clientY, point, shift: event.shiftKey, panX: state.panX, panY: state.panY, moved: false };
    return;
  }
  await tapAction(point, event.shiftKey);
}

function onPhoto(point) {
  return point.x >= 0 && point.y >= 0 && point.x < state.width && point.y < state.height;
}

async function tapAction(point, shift) {
  if (state.tool !== "select" && !onPhoto(point)) return;
  if (state.tool === "select") {
    const hit = hitSurface(point.x, point.y);
    if (hit) {
      selectSurface(hit.id);
      if (state.selectedId) revealSheet();
    } else if (state.selectedId) {
      state.selectedId = null;
      renderSurfaces();
      syncFinish();
      updateSwatchSelection();
      redraw();
    }
    return;
  }
  if (state.tool === "wand") {
    await runWand(point.x, point.y, shift);
    return;
  }
  if (state.tool === "dots") addLinePoint(point);
}

function touchTap(tap) {
  const now = performance.now();
  const last = state.lastTap;
  if (state.tool === "select" && last && now - last.time < 320 && Math.hypot(tap.x - last.x, tap.y - last.y) < 32) {
    state.lastTap = null;
    if (state.zoom > 1.05) {
      state.zoom = 1;
      state.panX = 0;
      state.panY = 0;
      fit();
    } else {
      setZoom(2.5, { x: tap.x, y: tap.y });
    }
    return;
  }
  state.lastTap = { x: tap.x, y: tap.y, time: now };
  tapAction(tap.point, tap.shift);
}

function beginStroke(point) {
  pushUndo();
  let current = selected();
  if (!current) {
    current = createCustomSurface();
    renderSurfaces();
    syncFinish();
  }
  state.painting = true;
  state.lastPoint = point;
  stroke(point, point);
}

function cancelStroke() {
  // A second finger means a pinch, so the first finger's brush mark is undone.
  if (!state.painting) return;
  state.painting = false;
  state.lastPoint = null;
  const shot = state.undo.pop();
  if (shot) restore(shot);
}

function onPointerMove(event) {
  state.zoomAnchor = { x: event.clientX, y: event.clientY };
  moveBrushCursor(event);
  if (state.pointers.has(event.pointerId)) state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (state.gesture) {
    if (state.pointers.size >= 2) {
      state.gestureTarget = pinchInfo();
      requestFrame();
    }
    return;
  }
  if (state.tap) {
    const dx = event.clientX - state.tap.x;
    const dy = event.clientY - state.tap.y;
    if (!state.tap.moved && Math.hypot(dx, dy) > 10) state.tap.moved = true;
    if (state.tap.moved && state.zoom > 1) {
      state.panX = state.tap.panX + dx;
      state.panY = state.tap.panY + dy;
      scheduleFit();
    }
    return;
  }
  if (state.panning && state.panOrigin) {
    state.panX = state.panOrigin.panX + (event.clientX - state.panOrigin.x);
    state.panY = state.panOrigin.panY + (event.clientY - state.panOrigin.y);
    scheduleFit();
    return;
  }
  if (!state.painting) return;
  const events = event.getCoalescedEvents ? event.getCoalescedEvents() : [];
  for (const each of events.length ? events : [event]) {
    const point = eventPoint(each);
    stroke(state.lastPoint, point);
    state.lastPoint = point;
  }
}

function onPointerUp(event) {
  const tap = state.tap;
  state.pointers.delete(event.pointerId);
  if (state.gesture) {
    if (state.pointers.size < 2) {
      state.gesture = null;
      state.gestureTarget = null;
      state.afterGesture = state.pointers.size > 0;
    }
    return;
  }
  if (state.afterGesture) {
    if (state.pointers.size === 0) state.afterGesture = false;
    return;
  }
  if (tap) {
    state.tap = null;
    if (!tap.moved && event.type === "pointerup") touchTap(tap);
    return;
  }
  if (state.panning) {
    state.panning = false;
    view.style.cursor = state.spaceDown ? "grab" : state.tool === "select" || state.tool === "wand" || state.tool === "dots" ? "crosshair" : "none";
  }
  const wasPainting = state.painting;
  state.painting = false;
  state.lastPoint = null;
  if (wasPainting) redraw();
}

function stampBrush(ctx, x, y) {
  const size = state.brushSize;
  const half = size / 2;
  ctx.beginPath();
  if (state.brushShape === "square") {
    ctx.rect(x - half, y - half, size, size);
  } else if (state.brushShape === "diamond") {
    ctx.moveTo(x, y - half);
    ctx.lineTo(x + half, y);
    ctx.lineTo(x, y + half);
    ctx.lineTo(x - half, y);
    ctx.closePath();
  } else {
    ctx.arc(x, y, half, 0, Math.PI * 2);
  }
  ctx.fill();
}

function stroke(from, to) {
  const current = selected();
  if (!current) return;
  const ctx = current.maskCanvas.getContext("2d");
  ctx.globalCompositeOperation = state.tool === "eraser" ? "destination-out" : "source-over";
  ctx.fillStyle = "rgba(255,255,255,1)";
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  const step = Math.max(1, state.brushSize / 3);
  const steps = Math.max(1, Math.ceil(distance / step));
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    stampBrush(ctx, from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t);
  }
  ctx.globalCompositeOperation = "source-over";
  bumpMask(current);
  scheduleRedraw();
}

function createCustomSurface() {
  state.customCount += 1;
  const surface = {
    id: `c${Date.now()}`,
    name: `Surface ${state.customCount}`,
    kind: "custom",
    confidence: 0,
    color: null,
    sheen: "eggshell",
    coverage: 0.92,
    shade: 0,
    included: true,
    maskCanvas: emptyMask(state.width, state.height),
    maskVersion: freshVersion(),
    feather: null,
  };
  state.surfaces.push(surface);
  state.selectedId = surface.id;
  return surface;
}

async function runWand(x, y, addToSelected) {
  setBusy(true);
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/wand`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y, tolerance: state.tolerance }),
    });
    if (!response.ok) throw new Error(await readError(response));
    const body = await response.json();
    if (body.area < 0.004) {
      toast(`That ${tapWord().toLowerCase()} didn't grab a surface. Raise wand reach or use the brush.`);
      return;
    }
    pushUndo();
    const maskCanvas = await maskFromPng(body.mask_png_base64);
    if (addToSelected && selected()) {
      const current = selected();
      const ctx = current.maskCanvas.getContext("2d");
      ctx.drawImage(maskCanvas, 0, 0);
      bumpMask(current);
    } else {
      subtractOthers(maskCanvas, null);
      state.customCount += 1;
      const surface = {
        id: `w${Date.now()}`,
        name: `Surface ${state.customCount}`,
        kind: "custom",
        confidence: 0,
        color: null,
        sheen: "eggshell",
        coverage: 0.92,
        shade: 0,
        included: true,
        maskCanvas,
        maskVersion: freshVersion(),
        feather: null,
      };
      state.surfaces.push(surface);
      state.selectedId = surface.id;
    }
    state.paintDirty = true;
    renderSurfaces();
    syncFinish();
    redraw();
    setStatus();
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

async function shrinkPhoto(file) {
  // Phone photos are often 3-10 MB. The server works at 1400 px, so send a 2000 px JPEG instead.
  if (file.size < 1.5 * 1024 * 1024 || !window.createImageBitmap) return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    if (bitmap.close) bitmap.close();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}

async function openFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    toast("Choose a photo.");
    return;
  }
  setBusy(true);
  try {
    const form = new FormData();
    form.append("file", await shrinkPhoto(file));
    const response = await fetch("/api/sessions", { method: "POST", body: form });
    if (!response.ok) throw new Error(await readError(response));
    await adoptSession(await response.json(), false);
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

async function openSample() {
  setBusy(true);
  try {
    const response = await fetch("/api/sample", { method: "POST" });
    if (!response.ok) throw new Error(await readError(response));
    await adoptSession(await response.json(), false);
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

function download() {
  if (!state.paintCanvas) return;
  if (state.paintDirty) rebuildPaint();
  state.paintCanvas.toBlob(async (blob) => {
    if (!blob) return;
    const file = new File([blob], "roomroller.png", { type: "image/png" });
    if (touchQuery.matches && navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: "RoomRoller" });
        return;
      } catch (error) {
        if (error.name === "AbortError") return;
      }
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "roomroller.png";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }, "image/png");
}

function syncBrushCursor() {
  const cursor = $("brush-cursor");
  cursor.classList.toggle("is-square", state.brushShape === "square");
  cursor.classList.toggle("is-diamond", state.brushShape === "diamond");
}

function moveBrushCursor(event) {
  const cursor = $("brush-cursor");
  if (cursor.hidden || !state.width) return;
  const stageRect = $("stage").getBoundingClientRect();
  const viewRect = view.getBoundingClientRect();
  const size = state.brushSize * (viewRect.width / view.width);
  syncBrushCursor();
  cursor.style.width = `${size}px`;
  cursor.style.height = `${size}px`;
  cursor.style.left = `${event.clientX - stageRect.left - size / 2}px`;
  cursor.style.top = `${event.clientY - stageRect.top - size / 2}px`;
}

function panelLimits() {
  return { min: 300, max: Math.max(420, Math.round(window.innerWidth * 0.78)) };
}

function setPanelWidth(px, save) {
  const limits = panelLimits();
  const width = Math.round(Math.min(limits.max, Math.max(limits.min, px)));
  document.documentElement.style.setProperty("--panel-width", `${width}px`);
  $("panel-resize").setAttribute("aria-valuenow", String(width));
  $("panel-resize").setAttribute("aria-valuemax", String(limits.max));
  if (save && preferencesAllowed()) localStorage.setItem(PANEL_KEY, String(width));
  fit();
  if ($("fold-mix").open) sizePicker();
}

function bindPanel() {
  const saved = preferencesAllowed() ? Number(localStorage.getItem(PANEL_KEY)) : 0;
  if (saved) setPanelWidth(saved, false);
  const handle = $("panel-resize");
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("is-dragging");
    const startX = event.clientX;
    const startWidth = document.querySelector(".panel").getBoundingClientRect().width;
    const move = (pointer) => setPanelWidth(startWidth + (startX - pointer.clientX), false);
    const up = (pointer) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.classList.remove("is-dragging");
      setPanelWidth(startWidth + (startX - pointer.clientX), true);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
  handle.addEventListener("keydown", (event) => {
    const current = document.querySelector(".panel").getBoundingClientRect().width;
    const step = event.shiftKey ? 96 : 40;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setPanelWidth(current + step, true);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setPanelWidth(current - step, true);
    }
  });
  document.querySelectorAll("summary .text-btn").forEach((button) => {
    button.addEventListener("pointerdown", (event) => event.stopPropagation());
    button.addEventListener("click", (event) => event.stopPropagation());
  });
  $("fold-mix").addEventListener("toggle", () => {
    if ($("fold-mix").open) requestAnimationFrame(() => sizePicker());
  });
}

function bind() {
  $("open-btn").addEventListener("click", () => $("file").click());
  $("empty-open").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", () => {
    openFile($("file").files[0]);
    $("file").value = "";
  });
  $("sample-btn").addEventListener("click", openSample);
  $("empty-sample").addEventListener("click", openSample);
  $("export-btn").addEventListener("click", download);
  $("save-btn").addEventListener("click", saveRoom);
  $("collection-btn").addEventListener("click", showCollection);
  $("empty-collection").addEventListener("click", showCollection);
  $("signout-btn").addEventListener("click", signOut);
  $("signin-btn").addEventListener("click", () => askSignIn(null));
  $("delete-account-btn").addEventListener("click", deleteAccount);
  $("collection-delete-account").addEventListener("click", deleteAccount);
  $("agree").addEventListener("change", syncSignInButton);
  $("consent-all").addEventListener("click", () => setConsent(true));
  $("consent-essential").addEventListener("click", () => {
    setConsent(false);
    renderRecent();
  });
  document.querySelectorAll("[data-cookie-settings]").forEach((button) => button.addEventListener("click", showConsent));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && (!$("signin").hidden || !$("collection").hidden)) closeDialogs();
  });
  $("signin-close").addEventListener("click", closeDialogs);
  $("collection-close").addEventListener("click", closeDialogs);
  $("collection-upload").addEventListener("change", () => {
    const files = [...$("collection-upload").files];
    $("collection-upload").value = "";
    if (files.length) uploadCollection(files);
  });
  $("undo-btn").addEventListener("click", undo);
  $("redo-btn").addEventListener("click", redo);
  $("add-surface").addEventListener("click", () => {
    if (!state.sessionId) return;
    if (state.draft && state.draft.length >= 3) addWallFromDots();
    else setTool("dots");
  });
  document.querySelectorAll("button[data-tool]").forEach((button) => {
    button.addEventListener("click", () => setTool(button.dataset.tool));
  });
  document.querySelectorAll("[data-sheen]").forEach((button) => {
    button.addEventListener("click", () => {
      const current = selected();
      if (!current) return;
      pushUndo();
      current.sheen = button.dataset.sheen;
      state.paintDirty = true;
      syncFinish();
      redraw();
    });
  });
  $("coverage").addEventListener("input", () => {
    const current = selected();
    if (!current) return;
    current.coverage = Number($("coverage").value) / 100;
    $("coverage-out").textContent = $("coverage").value;
    state.paintDirty = true;
    redraw();
  });
  $("shade").addEventListener("input", () => {
    const current = selected();
    if (!current) return;
    current.shade = Number($("shade").value);
    $("shade-out").textContent = $("shade").value;
    state.paintDirty = true;
    redraw();
  });
  $("coverage").addEventListener("pointerdown", () => {
    if (selected()) pushUndo();
  });
  $("shade").addEventListener("pointerdown", () => {
    if (selected()) pushUndo();
  });
  $("brush-size").addEventListener("input", () => {
    state.brushSize = Number($("brush-size").value);
  });
  document.querySelectorAll("[data-brush-shape]").forEach((button) => {
    button.addEventListener("click", () => {
      state.brushShape = button.dataset.brushShape;
      document.querySelectorAll("[data-brush-shape]").forEach((item) => {
        item.classList.toggle("is-on", item === button);
      });
      syncBrushCursor();
    });
  });
  $("tolerance").addEventListener("input", () => {
    state.tolerance = Number($("tolerance").value);
  });
  $("compare").addEventListener("input", () => {
    state.compare = Number($("compare").value) / 100;
    redraw();
  });
  $("clear-color-finish").addEventListener("click", () => $("clear-color").click());
  $("clear-color").addEventListener("click", () => {
    const current = selected();
    if (!current || !current.color) return;
    pushUndo();
    current.color = null;
    state.paintDirty = true;
    renderSurfaces();
    updateSwatchSelection();
    redraw();
  });
  $("apply-all").addEventListener("click", () => {
    const current = selected();
    if (!current || !current.color) return;
    pushUndo();
    state.surfaces.forEach((surface) => {
      if (surface.kind !== "wall" || surface.included === false) return;
      surface.color = current.color;
      surface.colorLabel = current.colorLabel || "";
      surface.sheen = current.sheen;
      surface.coverage = current.coverage;
      surface.shade = current.shade;
    });
    state.paintDirty = true;
    renderSurfaces();
    updateSwatchSelection();
    redraw();
  });
  $("search").addEventListener("input", () => {
    state.search = $("search").value;
    clearTimeout(loadColors.timer);
    loadColors.timer = setTimeout(() => loadColors(true), 180);
  });
  $("color-more").addEventListener("click", () => loadColors(false));
  $("add-wall").addEventListener("click", addWallFromDots);
  $("clear-dots").addEventListener("click", clearLines);
  $("custom-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const hex = parseHex($("custom-hex").value);
    if (!hex) {
      toast("Enter a color like #C45C26.");
      return;
    }
    const text = `#${$("custom-hex").value.replace("#", "").toUpperCase()}`;
    setPickerFromHex(text);
    chooseColor(text);
  });
  $("picker-sv").addEventListener("pointerdown", (event) => beginPick(event, $("picker-sv"), "sv"));
  $("picker-hue").addEventListener("pointerdown", (event) => beginPick(event, $("picker-hue"), "hue"));
  $("picker-sv").addEventListener("pointermove", movePick);
  $("picker-hue").addEventListener("pointermove", movePick);
  $("picker-sv").addEventListener("pointerup", finishPick);
  $("picker-hue").addEventListener("pointerup", finishPick);
  $("zoom-in").addEventListener("click", () => {
    setZoom(state.zoom * 1.25, zoomAnchor());
  });
  $("zoom-out").addEventListener("click", () => {
    setZoom(state.zoom / 1.25, zoomAnchor());
  });
  $("zoom-reset").addEventListener("click", () => {
    state.zoom = 1;
    state.panX = 0;
    state.panY = 0;
    fit();
  });
  $("stage").addEventListener("wheel", (event) => {
    if (!state.width) return;
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setZoom(state.zoom * factor, { x: event.clientX, y: event.clientY });
  }, { passive: false });
  $("stage").addEventListener("pointerdown", onPointerDown, true);
  $("stage").addEventListener("dblclick", (event) => {
    if (state.lastPointerType !== "mouse" || event.target.closest("button")) return;
    if (state.tool === "dots") {
      event.preventDefault();
      if (state.draft && state.draft.length >= 3) addWallFromDots();
      return;
    }
    if (state.tool === "select" || state.tool === "wand") {
      setZoom(state.zoom * (event.shiftKey ? 0.5 : 2), { x: event.clientX, y: event.clientY });
    }
  });
  $("stage").addEventListener("pointermove", onPointerMove);
  $("stage").addEventListener("pointerup", onPointerUp);
  $("stage").addEventListener("pointercancel", onPointerUp);
  $("stage").addEventListener("pointerleave", () => {
    $("brush-cursor").style.opacity = "0";
  });
  $("stage").addEventListener("pointerenter", () => {
    $("brush-cursor").style.opacity = "1";
  });

  const drop = $("drop");
  ["dragenter", "dragover"].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("drag");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove("drag");
    });
  });
  drop.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    openFile(file);
  });
  window.addEventListener("paste", (event) => {
    const items = event.clipboardData && event.clipboardData.files;
    if (items && items[0]) openFile(items[0]);
  });
  window.addEventListener("resize", () => {
    const current = document.querySelector(".panel").getBoundingClientRect().width;
    const limits = panelLimits();
    if (current > limits.max) setPanelWidth(limits.max, false);
    if (isMobile()) setSheet(state.sheet);
    scheduleFit();
    if ($("fold-mix").open) sizePicker();
  });
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea")) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === "y") {
      event.preventDefault();
      redo();
      return;
    }
    if (key === " " && state.sessionId) {
      event.preventDefault();
      state.spaceDown = true;
      if (!state.panning) view.style.cursor = "grab";
      return;
    }
    if (key === "v") setTool("select");
    if (key === "w") setTool("wand");
    if (key === "b") setTool("brush");
    if (key === "e") setTool("eraser");
    if (key === "d") setTool("dots");
    if (state.tool === "dots" && key === "enter") {
      event.preventDefault();
      addWallFromDots();
    }
    if (state.tool === "dots" && key === "backspace") {
      event.preventDefault();
      undoLinePoint();
    }
    if (state.tool === "dots" && key === "escape") {
      state.draft = null;
      redraw();
      syncLineButtons();
      setStatus();
    }
    if (key === "[") {
      state.brushSize = Math.max(4, state.brushSize - 4);
      $("brush-size").value = state.brushSize;
    }
    if (key === "]") {
      state.brushSize = Math.min(80, state.brushSize + 4);
      $("brush-size").value = state.brushSize;
    }
  });
  document.addEventListener("keyup", (event) => {
    if (event.key !== " ") return;
    state.spaceDown = false;
    if (!state.panning) {
      view.style.cursor = state.tool === "select" || state.tool === "wand" || state.tool === "dots" ? "crosshair" : "none";
    }
  });
}

function isMobile() {
  return mobileQuery.matches;
}

function sheetStops() {
  const height = window.innerHeight;
  return { peek: 112, half: Math.round(height * 0.54), full: Math.round(height * 0.86) };
}

function setSheet(name) {
  state.sheet = name;
  document.querySelector(".panel").style.setProperty("--sheet", `${sheetStops()[name]}px`);
}

function revealSheet() {
  if (isMobile() && state.sheet === "peek") setSheet("half");
}

function setSheetTab(id) {
  state.sheetTab = id;
  document.querySelectorAll(".panel > .fold").forEach((fold) => {
    const on = fold.id === id;
    fold.classList.toggle("is-tab", on);
    if (on) fold.open = true;
  });
  document.querySelectorAll("[data-sheet]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.sheet === id));
  });
  if (id === "fold-mix") requestAnimationFrame(sizePicker);
}

function setEditing(on) {
  state.editing = on;
  $("studio").classList.toggle("is-editing", on);
  $("edit-toggle").textContent = on ? "Done" : "Edit";
  $("edit-toggle").setAttribute("aria-pressed", String(on));
  if (!on && state.tool !== "select") setTool("select");
  scheduleFit();
}

function syncMobile() {
  const panel = document.querySelector(".panel");
  if (isMobile()) {
    setSheetTab(state.sheetTab);
    setSheet(state.sheet);
    if (state.colors.length) renderSwatches();
  } else {
    panel.style.removeProperty("--sheet");
    if (state.colors.length) renderSwatches();
    document.querySelectorAll(".panel > .fold").forEach((fold) => fold.classList.remove("is-tab"));
    toggleMenu(false);
  }
  scheduleFit();
}

function toggleMenu(open) {
  const top = document.querySelector(".top");
  const next = open == null ? !top.classList.contains("menu-open") : open;
  top.classList.toggle("menu-open", next);
  $("menu-btn").setAttribute("aria-expanded", String(next));
}

function bindMobile() {
  $("m-open").addEventListener("click", () => $("file").click());
  $("m-undo").addEventListener("click", undo);
  $("m-redo").addEventListener("click", redo);
  $("edit-toggle").addEventListener("click", () => setEditing(!state.editing));
  $("brand-select").addEventListener("change", () => {
    state.brand = $("brand-select").value;
    loadColors(true);
  });
  $("menu-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#top-actions") && !event.target.closest("#menu-btn")) toggleMenu(false);
  });
  $("top-actions").addEventListener("click", (event) => {
    if (isMobile() && event.target.closest("button:not(#signout-btn)")) toggleMenu(false);
  });
  document.querySelectorAll("[data-sheet]").forEach((button) => {
    button.addEventListener("click", () => {
      setSheetTab(button.dataset.sheet);
      revealSheet();
    });
  });
  document.querySelectorAll(".panel > .fold > summary").forEach((summary) => {
    summary.addEventListener("click", (event) => {
      if (isMobile()) event.preventDefault();
    });
  });

  const panel = document.querySelector(".panel");
  const handle = $("sheet-handle");
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const startY = event.clientY;
    const startHeight = panel.getBoundingClientRect().height;
    const stops = sheetStops();
    let moved = false;
    panel.classList.add("is-dragging");
    const move = (pointer) => {
      const dy = pointer.clientY - startY;
      if (Math.abs(dy) > 6) moved = true;
      const height = Math.min(stops.full, Math.max(stops.peek, startHeight - dy));
      panel.style.setProperty("--sheet", `${height}px`);
    };
    const up = (pointer) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      panel.classList.remove("is-dragging");
      if (!moved) {
        setSheet(state.sheet === "full" ? "half" : state.sheet === "half" ? "full" : "half");
        return;
      }
      const height = Math.min(stops.full, Math.max(stops.peek, startHeight - (pointer.clientY - startY)));
      const nearest = Object.entries(stops).sort((a, b) => Math.abs(a[1] - height) - Math.abs(b[1] - height))[0][0];
      setSheet(nearest);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSheet(state.sheet === "full" ? "half" : "full");
    }
  });

  const hold = $("hold-compare");
  const showBefore = (event) => {
    event.preventDefault();
    hold.classList.add("is-on");
    state.compare = 0;
    redraw();
  };
  const showAfter = () => {
    if (!hold.classList.contains("is-on")) return;
    hold.classList.remove("is-on");
    state.compare = Number($("compare").value) / 100;
    redraw();
  };
  hold.addEventListener("pointerdown", showBefore);
  ["pointerup", "pointercancel", "pointerleave"].forEach((name) => hold.addEventListener(name, showAfter));
  hold.addEventListener("contextmenu", (event) => event.preventDefault());

  if (window.ResizeObserver) new ResizeObserver(() => scheduleFit()).observe($("stage"));
  mobileQuery.addEventListener("change", syncMobile);
  if (touchQuery.matches) $("empty-title").textContent = "Start with a photo of your room.";
  if (isMobile()) $("search").placeholder = "Search name or code";
  syncMobile();
}

async function boot() {
  bindPanel();
  bind();
  bindMobile();
  renderRecent();
  syncLineButtons();
  if ($("fold-mix").open) sizePicker();
  setPickerFromHex("#C45C26");
  loadAccount();
  if (!consent()) $("consent").hidden = false;
  await loadColors(true);
}

async function loadAccount() {
  try {
    const response = await fetch("/api/auth/me");
    if (!response.ok) throw new Error(await readError(response));
    const body = await response.json();
    state.clientId = body.client_id || "";
    state.user = body.user || null;
    state.legalVersion = body.legal_version || "";
  } catch {
    state.clientId = "";
  }
  renderAccount();
}

function loadGoogle() {
  // Google's script is only fetched after someone chooses to sign in and agrees to the terms.
  if (state.googleReady) return state.googleReady;
  state.googleReady = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GOOGLE_SCRIPT;
    script.async = true;
    script.onload = () => {
      google.accounts.id.initialize({
        client_id: state.clientId,
        callback: onGoogleCredential,
        auto_select: false,
        cancel_on_tap_outside: true,
      });
      resolve();
    };
    script.onerror = () => {
      state.googleReady = null;
      reject(new Error("Google sign-in couldn't load. Check your connection and try again."));
    };
    document.head.appendChild(script);
  });
  return state.googleReady;
}

function renderGoogleButton(node, text) {
  if (!window.google?.accounts?.id || !state.clientId) return;
  node.innerHTML = "";
  google.accounts.id.renderButton(node, { theme: "outline", size: "large", shape: "pill", text });
}

async function syncSignInButton() {
  const node = $("signin-button");
  clearTimeout(askSignIn.timer);
  $("signin-problem").hidden = true;
  if (!$("agree").checked) {
    node.innerHTML = '<p class="hint">Check the box above to continue with Google.</p>';
    return;
  }
  node.innerHTML = '<p class="hint">Loading Google sign-in…</p>';
  try {
    await loadGoogle();
  } catch (error) {
    node.innerHTML = "";
    $("signin-problem").textContent = error.message;
    $("signin-problem").hidden = false;
    return;
  }
  if (!$("agree").checked || $("signin").hidden) return;
  renderGoogleButton(node, "continue_with");
  askSignIn.timer = setTimeout(() => {
    if ($("signin").hidden || node.querySelector("iframe")) return;
    $("signin-problem").textContent = `Google's sign-in button didn't load here. Add ${window.location.origin} to the Authorized JavaScript origins of the Google client, then reload.`;
    $("signin-problem").hidden = false;
  }, 4000);
}

function renderAccount() {
  const user = state.user;
  document.body.classList.toggle("signed-in", Boolean(user));
  $("account-user").hidden = !user;
  $("signin-btn").hidden = Boolean(user) || !state.clientId;
  $("account-off").hidden = Boolean(user) || Boolean(state.clientId);
  if (user) {
    $("account-name").textContent = user.name;
    $("account-initial").textContent = (user.name || "?").trim().charAt(0).toUpperCase();
  }
}

async function deleteAccount() {
  if (!state.user) return;
  if (!window.confirm("Delete your RoomRoller account and every saved room? This can't be undone.")) return;
  try {
    const response = await fetch("/api/account", { method: "DELETE" });
    if (!response.ok) throw new Error(await readError(response));
    window.google?.accounts?.id?.disableAutoSelect();
    signedOut();
    toast("Your account and saved rooms were deleted.");
  } catch (error) {
    toast(error.message);
  }
}

async function onGoogleCredential(response) {
  try {
    const result = await fetch("/api/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        credential: response.credential,
        accepted_terms: $("agree").checked,
        terms_version: state.legalVersion,
      }),
    });
    if (!result.ok) throw new Error(await readError(result));
    state.user = (await result.json()).user;
    renderAccount();
    $("signin").hidden = true;
    toast(`Signed in as ${state.user.name}.`);
    const next = state.afterSignIn;
    state.afterSignIn = null;
    if (next) next();
  } catch (error) {
    toast(error.message);
  }
}

async function signOut() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {
    // The cookie is cleared server-side; a network blip here still signs out locally.
  }
  window.google?.accounts?.id?.disableAutoSelect();
  signedOut();
  toast("Signed out.");
}

function signedOut() {
  state.user = null;
  state.collectionId = null;
  state.collectionName = "";
  $("collection").hidden = true;
  renderAccount();
}

function askSignIn(next) {
  if (!state.clientId) {
    toast("Google sign-in isn't set up on this server yet.");
    return;
  }
  state.afterSignIn = next;
  state.lastFocus = document.activeElement;
  $("signin").hidden = false;
  syncSignInButton();
  $("agree").focus();
}

function closeDialogs() {
  const open = !$("signin").hidden || !$("collection").hidden;
  $("signin").hidden = true;
  $("collection").hidden = true;
  state.afterSignIn = null;
  if (open && state.lastFocus && document.contains(state.lastFocus)) state.lastFocus.focus();
}

async function checkSignedIn(response, next) {
  if (response.status !== 401) return false;
  signedOut();
  askSignIn(next);
  return true;
}

function canvasBase64(canvas) {
  return canvas.toDataURL("image/png").split(",")[1];
}

async function saveRoom() {
  if (!state.sessionId) return;
  if (!state.user) {
    askSignIn(saveRoom);
    return;
  }
  let name = state.collectionName;
  if (!state.collectionId) {
    name = window.prompt("Name this room", "Room");
    if (name == null) return;
  }
  const surfaces = state.surfaces.map((surface) => ({
    name: surface.name,
    kind: surface.kind,
    color: surface.color,
    color_label: surface.colorLabel || "",
    sheen: surface.sheen,
    coverage: surface.coverage,
    shade: surface.shade,
    included: surface.included !== false,
    mask_png_base64: canvasBase64(surface.maskCanvas),
  }));
  const creating = !state.collectionId;
  const url = creating
    ? `/api/sessions/${state.sessionId}/collection`
    : `/api/collection/${state.collectionId}?session_id=${state.sessionId}`;
  try {
    const response = await fetch(url, {
      method: creating ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name || "Room", surfaces }),
    });
    if (await checkSignedIn(response, saveRoom)) return;
    if (!response.ok) throw new Error(await readError(response));
    const body = await response.json();
    state.collectionId = body.id;
    state.collectionName = body.name;
    toast(creating ? "Saved to your collection." : "Updated in your collection.");
  } catch (error) {
    toast(error.message);
  }
}

async function uploadCollection(files) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file, file.webkitRelativePath || file.name));
  try {
    const response = await fetch("/api/collection/import", { method: "POST", body });
    if (await checkSignedIn(response, showCollection)) return;
    if (!response.ok) throw new Error(await readError(response));
    const saved = (await response.json()).rooms;
    const names = saved.map((room) => room.name).join(", ");
    toast(saved.length === 1 ? `Added ${names}.` : `Added ${saved.length} saved rooms.`);
    await showCollection();
  } catch (error) {
    toast(error.message);
  }
}

async function showCollection() {
  if (!state.user) {
    askSignIn(showCollection);
    return;
  }
  state.lastFocus = document.activeElement;
  $("collection").hidden = false;
  $("collection-close").focus();
  const list = $("collection-list");
  list.innerHTML = "";
  try {
    const response = await fetch("/api/collection");
    if (await checkSignedIn(response, showCollection)) return;
    if (!response.ok) throw new Error(await readError(response));
    const rooms = await response.json();
    if (!rooms.length) {
      const empty = document.createElement("li");
      empty.className = "collection-empty";
      empty.textContent = "No saved rooms yet.";
      list.appendChild(empty);
      return;
    }
    rooms.forEach((room) => {
      const item = document.createElement("li");
      item.className = "collection-item";
      const image = document.createElement("img");
      image.alt = `Preview of ${room.name}`;
      image.src = `/api/collection/${room.id}/thumb`;
      const text = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = room.name;
      const meta = document.createElement("small");
      const when = new Date(room.saved_at);
      const stamp = Number.isNaN(when.getTime())
        ? ""
        : when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      meta.textContent = `${room.walls} wall${room.walls === 1 ? "" : "s"}${stamp ? ` · ${stamp}` : ""}`;
      text.append(title, meta);
      const actions = document.createElement("div");
      const open = document.createElement("button");
      open.type = "button";
      open.className = "text-btn";
      open.textContent = "Open";
      open.addEventListener("click", () => openSaved(room.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-btn";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeSaved(room.id, room.name));
      actions.append(open, remove);
      item.append(image, text, actions);
      list.appendChild(item);
    });
  } catch (error) {
    toast(error.message);
  }
}

async function openSaved(roomId) {
  setBusy(true);
  try {
    const response = await fetch(`/api/collection/${roomId}/open`, { method: "POST" });
    if (await checkSignedIn(response, showCollection)) return;
    if (!response.ok) throw new Error(await readError(response));
    const body = await response.json();
    await adoptSession(body, false);
    state.collectionId = body.collection_id;
    state.collectionName = body.name;
    $("collection").hidden = true;
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

async function removeSaved(roomId, name) {
  if (!window.confirm(`Remove ${name} from the collection?`)) return;
  try {
    const response = await fetch(`/api/collection/${roomId}`, { method: "DELETE" });
    if (await checkSignedIn(response, showCollection)) return;
    if (!response.ok) throw new Error(await readError(response));
    if (state.collectionId === roomId) {
      state.collectionId = null;
      state.collectionName = "";
    }
    await showCollection();
  } catch (error) {
    toast(error.message);
  }
}

boot();
