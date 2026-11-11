/** Rectangle selection and CSV export for visible routes. Desktop only. */
import { getManifest, lookupFlight } from "./data.js";
import { isDesktopLayout } from "./lib/layout.js";
import { FLIGHT_CSV_COLUMNS, flightToRow, csvLine } from "./csv.js";

let map = null;
let btn = null;
let overlay = null;
let selecting = false;
let startPx = null;
let ready = false;

export function initRectExport() {
  if (!isDesktopLayout()) return;
  map = window.__map;
  if (!map) return;

  btn = document.getElementById("rect-export-btn");
  if (!btn) return;

  overlay = document.createElement("div");
  overlay.id = "rect-select-overlay";
  document.getElementById("map").appendChild(overlay);

  btn.style.display = "block";
  ready = true;
}

/** Trigger for the Export menu's "Select area to export" item (ExportMenu's
 * onRectExport prop). Guarded so a click before initRectExport(map) has run
 * (e.g. desktop map still loading) is a no-op, matching the previous
 * behavior where the click listener simply didn't exist yet. */
export function triggerRectExport() {
  if (!ready) return;
  enterSelectionMode();
}

function enterSelectionMode() {
  if (selecting) return;
  selecting = true;
  btn.classList.add("active");
  document.getElementById("rect-select-hint")?.classList.remove("hidden");
  document.addEventListener("keydown", onKeyDown);
  map.getCanvas().style.cursor = "crosshair";
  map.dragPan.disable();

  const canvas = map.getCanvas();
  canvas.addEventListener("mousedown", onMouseDown);
}

function exitSelectionMode() {
  selecting = false;
  btn.classList.remove("active");
  document.getElementById("rect-select-hint")?.classList.add("hidden");
  document.removeEventListener("keydown", onKeyDown);
  map.getCanvas().style.cursor = "";
  map.dragPan.enable();
  overlay.style.display = "none";

  const canvas = map.getCanvas();
  canvas.removeEventListener("mousedown", onMouseDown);
}

function onKeyDown(e) {
  if (e.key === "Escape") exitSelectionMode();
}

function onMouseDown(e) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();

  const rect = map.getCanvas().getBoundingClientRect();
  startPx = { x: e.clientX - rect.left, y: e.clientY - rect.top };

  overlay.style.left = startPx.x + "px";
  overlay.style.top = startPx.y + "px";
  overlay.style.width = "0";
  overlay.style.height = "0";
  overlay.style.display = "block";

  const onMove = (ev) => {
    const cx = ev.clientX - rect.left;
    const cy = ev.clientY - rect.top;
    const x = Math.min(startPx.x, cx);
    const y = Math.min(startPx.y, cy);
    const w = Math.abs(cx - startPx.x);
    const h = Math.abs(cy - startPx.y);
    overlay.style.left = x + "px";
    overlay.style.top = y + "px";
    overlay.style.width = w + "px";
    overlay.style.height = h + "px";
  };

  const onUp = (ev) => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);

    const endRect = map.getCanvas().getBoundingClientRect();
    const endPx = { x: ev.clientX - endRect.left, y: ev.clientY - endRect.top };
    const minW = 10, minH = 10;
    if (Math.abs(endPx.x - startPx.x) < minW || Math.abs(endPx.y - startPx.y) < minH) {
      exitSelectionMode();
      return;
    }

    const sw = map.unproject([
      Math.min(startPx.x, endPx.x),
      Math.max(startPx.y, endPx.y),
    ]);
    const ne = map.unproject([
      Math.max(startPx.x, endPx.x),
      Math.min(startPx.y, endPx.y),
    ]);
    const bounds = { south: sw.lat, north: ne.lat, west: sw.lng, east: ne.lng };

    exitSelectionMode();
    showOverlay();
    requestAnimationFrame(() => {
      exportFlightsInRect(bounds);
      setTimeout(hideOverlay, 1500);
    });
  };

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function showOverlay() {
  document.getElementById("updating-overlay")?.classList.remove("hidden");
}

function hideOverlay() {
  document.getElementById("updating-overlay")?.classList.add("hidden");
}

function exportFlightsInRect(bounds) {
  const m = getManifest();
  if (!m) return;

  const sw = [bounds.west, bounds.south];
  const ne = [bounds.east, bounds.north];
  const swPx = map.project(sw);
  const nePx = map.project(ne);

  const features = map.queryRenderedFeatures(
    [swPx, nePx],
    { layers: ["routes-line"] },
  );

  const seenIds = new Set();
  const rows = [];

  for (const f of features) {
    const id = f.properties?.id;
    if (id == null || seenIds.has(id)) continue;
    seenIds.add(id);

    const flight = lookupFlight(id);
    if (!flight) continue;

    rows.push(flightToRow(flight, m.types));
  }

  rows.sort((a, b) => a.date.localeCompare(b.date) || a.hex.localeCompare(b.hex));

  const params = new URLSearchParams(window.location.search);
  const paramStr = params.toString();

  const bStr = [
    `N${bounds.north.toFixed(2)}`,
    `S${bounds.south.toFixed(2)}`,
    `W${bounds.west.toFixed(2)}`,
    `E${bounds.east.toFixed(2)}`,
  ].join("_");

  const filename = `flyover_${bStr}_${rows.length}flights.csv`;

  const header = [
    `# Flyover Country export`,
    `# Bounds: ${bounds.south.toFixed(4)}°N to ${bounds.north.toFixed(4)}°N, ${bounds.west.toFixed(4)}°E to ${bounds.east.toFixed(4)}°E`,
    paramStr ? `# Query: ${paramStr}` : `# Query: (defaults)`,
    `# ${rows.length} flights`,
  ].join("\n");

  const csvHeader = csvLine(FLIGHT_CSV_COLUMNS);
  const csvRows = rows.map(r => csvLine(FLIGHT_CSV_COLUMNS.map(c => r[c])));

  const csv = [header, csvHeader, ...csvRows].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
