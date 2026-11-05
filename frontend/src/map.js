/** MapLibre map setup and layer management. */
import { getFocusConfig, getManifest, lookupFlight, fillHeadlineStats, roleLabel, dayToDateStr, getHexRoleMask, rolesToMask } from "./data.js";
import { getDayRange, getRoles } from "./filters.js";
import { ALT_COLORS } from "./lib/alt-colors.js";
import { colorForLevel } from "./lib/permissiveness-colors.js";
import { escapeHtml, escapeHtmlWithBreaks } from "./text.js";

let map = null;
let isMobile = false;
// A finger never "leaves" a country the way a mouse does, so the hover
// fill would stick to the last-tapped country until the next tap anywhere —
// which reads as "still selected" after a deselect. No hover fill on touch.
let coarsePointer = false;
let countriesLoaded = false;
let traceInteractionEnabled = false;
let countryClickEnabled = true;
let onCountryClick = null;
let onFlightOpen = null;
let onExploreMiss = null;
let offLimitsSet = new Set();
let hoveredCountryCc = null;
const GULF_TRIGGER = new Set(["SA", "KW"]);
const GULF_CCS = ["SA", "KW"];
const HIGHLIGHT_COLOR = "#ffffff";

export function initMap(opts) {
  isMobile = opts?.mobile || false;
  coarsePointer = isMobile || !!window.matchMedia?.("(pointer: coarse)").matches;
  onCountryClick = opts?.onCountryClick || null;
  onFlightOpen = opts?.onFlightOpen || null;
  onExploreMiss = opts?.onExploreMiss || null;
  map = window.__map;
  return map;
}

export function getMap() { return map; }
export function setTraceInteraction(enabled) { traceInteractionEnabled = enabled; if (!enabled) clearHover(); }
export function setCountryClickEnabled(enabled) {
  countryClickEnabled = enabled;
  if (!enabled && hoveredCountryCc && countriesLoaded) {
    hoveredCountryCc = null;
    map.setFilter("country-hover", ["==", ["get", "cc"], ""]);
    if (tooltipEl) {
      tooltipEl.style.display = "none";
      tooltipEl.classList.remove("country-tip");
    }
  }
}

/** cc -> fill colour, from each country's permissiveness level
 * (permissiveness-colors.js). */
function buildCountryColorMap(fc) {
  if (!fc) return {};
  const ccColor = {};
  for (const [cc, info] of Object.entries(fc.countries)) {
    ccColor[cc] = colorForLevel(info.permissiveness, fc.permissivenessLevels);
  }
  return ccColor;
}

/** Load country boundaries and add permissiveness-coloured layers. */
export async function loadCountryBoundaries(prefetched) {
  try {
    let raw = prefetched;
    if (!raw) {
      const res = await fetch("/data/boundaries.geojson");
      if (!res.ok) return;
      raw = await res.json();
    }
    const fc = getFocusConfig();
    const ccColor = buildCountryColorMap(fc);
    const geojson = {
      ...raw,
      features: raw.features.filter(f => ccColor[f.properties.cc]),
    };

    const colorExpr = ["match", ["get", "cc"]];
    for (const [cc, color] of Object.entries(ccColor)) {
      colorExpr.push(cc, color);
    }
    // match's required default. Unreachable in practice: the geojson above is
    // filtered to features whose cc is in ccColor, so every rendered feature
    // hits a branch.
    colorExpr.push(colorForLevel(2, fc.permissivenessLevels));

    map.addSource("countries", {
      type: "geojson",
      data: geojson,
      promoteId: "cc",
    });

    // Solid colour fill, kept low-opacity and rendered below the
    // flight-line layers (see the beforeLayer wiring in addRoutes below) so
    // it reads as ground context rather than competing with the routes.
    map.addLayer({
      id: "country-fill",
      type: "fill",
      source: "countries",
      paint: {
        "fill-color": colorExpr,
        "fill-opacity": 0.18,
      },
    });

    // Selection/hover affordances: a fixed neutral highlight colour, not
    // derived from permissiveness — these three layers are UI feedback
    // (this country is selected / hovered), not a second data-carrying
    // colour scheme.
    map.addLayer({
      id: "country-highlight",
      type: "fill",
      source: "countries",
      paint: {
        "fill-color": HIGHLIGHT_COLOR,
        "fill-opacity": 0.22,
      },
      filter: ["==", ["get", "cc"], ""],
    });

    map.addLayer({
      id: "country-highlight-border",
      type: "line",
      source: "countries",
      paint: {
        "line-color": HIGHLIGHT_COLOR,
        "line-width": 1.5,
        "line-opacity": 0.8,
      },
      filter: ["==", ["get", "cc"], ""],
    });

    map.addLayer({
      id: "country-hover",
      type: "fill",
      source: "countries",
      paint: {
        "fill-color": HIGHLIGHT_COLOR,
        "fill-opacity": 0.25,
      },
      filter: ["==", ["get", "cc"], ""],
    });

    // Off-limits = never clickable/selectable: none of the three
    // interaction flags is set (UA/BY/RU today — see countryEntry's doc
    // comment in data.js).
    offLimitsSet = new Set(
      Object.entries(fc?.countries || {})
        .filter(([, info]) => !(info.stats || info.whatif || info.info))
        .map(([cc]) => cc)
    );

    countriesLoaded = true;
  } catch (err) { console.error("boundaries:", err); }
}

/** Highlight a country on the map, or clear highlight if null. */
export function highlightCountry(cc) {
  if (!countriesLoaded) return;
  const filter = cc ? ["==", ["get", "cc"], cc] : ["==", ["get", "cc"], ""];
  map.setFilter("country-highlight", filter);
  map.setFilter("country-highlight-border", filter);
}

/** Clear any selected/hovered country state and dismiss any open tooltip or
 * popup. Used by the guided tour, which takes over the map's visual state
 * and must not show whatever the user was hovering/had selected before it
 * started. */
export function clearCountryInteraction() {
  if (!map) return;
  hoveredCountryCc = null;
  if (countriesLoaded) {
    const none = ["==", ["get", "cc"], ""];
    map.setFilter("country-hover", none);
    map.setFilter("country-highlight", none);
    map.setFilter("country-highlight-border", none);
  }
  if (tooltipEl) {
    tooltipEl.style.display = "none";
    tooltipEl.classList.remove("country-tip");
  }
  snappedPopup?.remove();
}

let routesLoaded = false;

/** Add pre-loaded corridor GeoJSON as a map layer. */
export function addRoutes(geojson) {
  if (!geojson || routesLoaded) return;
  const expanded = expandBandedRoutes(geojson);
  const beforeLayer = countriesLoaded ? "country-hover" : undefined;
  map.addSource("routes", { type: "geojson", data: expanded, generateId: true });
  const CORRIDOR_COLOR = [
    "match", ["get", "band"],
    0, ALT_COLORS[0],   1, ALT_COLORS[1],   2, ALT_COLORS[2],   3, ALT_COLORS[3],
    4, ALT_COLORS[4],   5, ALT_COLORS[5],   6, ALT_COLORS[6],   7, ALT_COLORS[7],
    8, ALT_COLORS[8],   9, ALT_COLORS[9],   10, ALT_COLORS[10], 11, ALT_COLORS[11],
    12, ALT_COLORS[12], 13, ALT_COLORS[13], 14, ALT_COLORS[14], 15, ALT_COLORS[15],
    ALT_COLORS[0],
  ];
  map.addLayer({
    id: "routes-line",
    type: "line",
    source: "routes",
    paint: {
      "line-color": CORRIDOR_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.5, 6, 2.5, 8, 3, 10, 4, 12, 5],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.03, 6, 0.06, 8, 0.15, 10, 0.35, 12, 0.6],
      "line-blur": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 6, 1, 10, 1.5, 12, 0],
    },
    layout: {
      visibility: "visible",
      "line-cap": "round",
      "line-join": "round",
    },
  }, beforeLayer);
  routesLoaded = true;
}

/** Split corridor features by bi[] band indices into per-band sub-segments. */
function expandBandedRoutes(geojson) {
  const features = [];
  for (const f of geojson.features) {
    const coords = f.geometry.coordinates;
    const bi = f.properties.bi;
    if (!bi || bi.length < 2) {
      features.push({ ...f, properties: { ...f.properties, band: bi?.[0] || 0 } });
      continue;
    }
    const { id, cc, rs, day, thc, the } = f.properties;
    let start = 0;
    let curBand = bi[0];
    for (let i = 1; i < bi.length; i++) {
      if (bi[i] !== curBand) {
        if (i - start >= 2) {
          features.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: coords.slice(start, i) },
            properties: { id, cc, rs, day, thc, the, band: curBand },
          });
        }
        start = i - 1;
        curBand = bi[i];
      }
    }
    if (bi.length - start >= 2) {
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords.slice(start) },
        properties: { id, cc, rs, day, thc, the, band: curBand },
      });
    }
  }
  return { type: "FeatureCollection", features };
}

/** Set corridor country focus and paint style (does NOT refilter — call refilterAll). */
export function highlightRoutes(cc) {
  if (!routesLoaded) return;
  currentRouteCc = cc;
  setLayerVisibility("routes-line", true);
  const base = routeBasePaint();
  map.setPaintProperty("routes-line", "line-opacity", base.opacity);
  map.setPaintProperty("routes-line", "line-width", base.width);
}


let landingsLoaded = false;
let snappedRawFeatures = null;
let airportsMeta = null;
let snappedPopup = null;

let overLandingsCircle = false;

export async function loadLandings() {
  try {
    const [geoRes, metaRes] = await Promise.all([
      fetch("/data/landings.geojson"),
      fetch("/data/airports_meta.json"),
    ]);
    if (!geoRes.ok || !metaRes.ok) return;
    snappedRawFeatures = (await geoRes.json()).features;
    airportsMeta = await metaRes.json();

    const beforeId = map.getLayer("routes-line") ? "routes-line" : undefined;
    map.addSource("landings", { type: "geojson", data: _buildFilteredLandings() });
    map.addLayer({
      id: "landings-circles",
      type: "circle",
      source: "landings",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["get", "landings"],
          1, 4, 10, 8, 50, 14, 100, 20, 500, 30],
        "circle-color": "#e65100",
        "circle-opacity": 0.7,
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "#fff",
        "circle-stroke-opacity": 0.8,
      },
      layout: { visibility: "none" },
    }, beforeId);

    snappedPopup = new maplibregl.Popup({
      closeButton: false, closeOnClick: false,
      maxWidth: "280px", offset: 10,
    });

    map.on("mouseenter", "landings-circles", (e) => {
      overLandingsCircle = true;
      if (tooltipEl) {
        tooltipEl.style.display = "none";
        tooltipEl.classList.remove("country-tip");
      }
      if (hoveredCountryCc) {
        map.setFilter("country-hover", ["==", ["get", "cc"], ""]);
        hoveredCountryCc = null;
      }
      map.getCanvas().style.cursor = "pointer";
      const p = e.features[0].properties;
      const meta = airportsMeta[p.icao] || {};
      const facilityType = (meta.type || "").replace(/_/g, " ");
      const milType = meta.mil_type;
      const milBadge = (milType === "military" || milType === "dual_use")
        ? `<div style="color:#ff6f00;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.3px">${milType === "military" ? "Military" : "Dual-use"}</div>`
        : "";
      // Airport metadata (name/municipality/type) comes from OurAirports/OSM
      // CSVs, not our own data — escape it like any other third-party text
      // interpolated into markup.
      const html = `<div style="font:12px/1.4 sans-serif;max-width:260px;color:#222">
        <div style="font-weight:600;font-size:13px;color:#000">${escapeHtml(meta.name || "Unknown")} (${escapeHtml(p.icao)})</div>
        <div style="color:#555;font-size:11px">${escapeHtml(facilityType)}${meta.municipality ? " • " + escapeHtml(meta.municipality) : ""}</div>
        ${milBadge}
        <hr style="margin:4px 0;border:0;border-top:1px solid #ccc">
        <div>Landings: <b>${p.landings}</b> (${p.aircraft} distinct aircraft)</div>
      </div>`;
      snappedPopup.setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    map.on("mouseleave", "landings-circles", () => {
      overLandingsCircle = false;
      map.getCanvas().style.cursor = "";
      snappedPopup.remove();
    });

    landingsLoaded = true;
  } catch (err) { console.error("landings:", err); }
}

export function showLandings(mode) {
  if (!landingsLoaded) return;
  currentGroundCc = mode || null;
  if (mode) {
    setLayerVisibility("landings-circles", true);
    _refilterLandings();
  } else {
    setLayerVisibility("landings-circles", false);
    snappedPopup?.remove();
  }
}

const LANDINGS_LAYER_IDS = ["landings-circles"];
let tourLandingsVisibility = null;

/** Hide landings layers for the tour, remembering prior visibility so
 * restoreLandingsAfterTour can restore. */
export function hideLandingsForTour() {
  if (!map) return;
  tourLandingsVisibility = {};
  for (const id of LANDINGS_LAYER_IDS) {
    if (!map.getLayer(id)) continue;
    tourLandingsVisibility[id] = map.getLayoutProperty(id, "visibility");
    setLayerVisibility(id, false);
  }
  snappedPopup?.remove();
}

/** Restore landings layer visibility after the tour. */
export function restoreLandingsAfterTour() {
  if (!map || !tourLandingsVisibility) return;
  for (const [id, visibility] of Object.entries(tourLandingsVisibility)) {
    if (map.getLayer(id)) setLayerVisibility(id, visibility === "visible");
  }
  tourLandingsVisibility = null;
}

function _buildFilteredLandings() {
  if (!snappedRawFeatures) return { type: "FeatureCollection", features: [] };
  const hrm = getHexRoleMask();
  const activeMask = rolesToMask(getRoles());
  const [dayMin, dayMax] = getDayRange();
  const ccFilter = currentGroundCc && currentGroundCc !== true ? currentGroundCc : null;

  const features = [];
  for (const f of snappedRawFeatures) {
    const p = f.properties;
    if (ccFilter && p.cc !== ccFilter) continue;
    const hArr = typeof p.h === "string" ? JSON.parse(p.h) : p.h;
    const dArr = typeof p.d === "string" ? JSON.parse(p.d) : p.d;
    const seen = new Set();
    let events = 0;
    for (let i = 0; i < hArr.length; i++) {
      const d = dArr[i];
      if (d < dayMin || d > dayMax) continue;
      const hex = hArr[i];
      const hexMask = hrm?.get(hex) || 0;
      if ((hexMask & activeMask) === 0) continue;
      seen.add(hex);
      events++;
    }
    if (events === 0) continue;
    features.push({
      type: "Feature",
      geometry: f.geometry,
      properties: { icao: p.icao, landings: events, aircraft: seen.size, cc: p.cc },
    });
  }
  return { type: "FeatureCollection", features };
}

function _refilterLandings() {
  if (!landingsLoaded || !snappedRawFeatures) return;
  map.getSource("landings")?.setData(_buildFilteredLandings());
}

function setLayerVisibility(layerId, visible) {
  if (map.getLayer(layerId)) {
    map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }
}

let currentBaseFilter = null;
let currentRouteCc = null;
let currentGroundCc = null;

/** Apply a filter expression to all filterable layers. */
export function applyFilter(filterExpr) {
  currentBaseFilter = filterExpr;
  refilterAll();
}

export function refilterAll() {
  refilterRoutes();
  _refilterLandings();
}

function refilterRoutes() {
  if (!routesLoaded || !currentBaseFilter) return;
  if (!map.getLayer("routes-line")) return;
  const parts = [...currentBaseFilter];
  if (currentRouteCc) parts.push(["in", currentRouteCc, ["get", "cc"]]);
  map.setFilter("routes-line", parts);
}

// -- flight interaction (hover tooltip + click to ADS-B Exchange) -------------

let hoveredId = null;
let tooltipEl = null;

function tipPos(point) {
  const r = map.getContainer().getBoundingClientRect();
  return { x: r.left + point.x + 12, y: r.top + point.y - 12 };
}

function formatFlightTooltip(flight) {
  const m = getManifest();
  const role = (flight.rs || []).map(roleLabel).join(", ");
  const type = m.types?.[flight.ti]?.label || m.types?.[flight.ti]?.code || "";
  const origin = flight.oa || "?";
  const dest = flight.da || "?";
  const alt = flight.kft != null ? `FL${flight.kft * 10}` : "";
  const km = flight.path ? `${flight.path} km` : "";
  const date = m.data_start && flight.day != null
    ? dayToDateStr(flight.day) : "";
  const icao = flight.hex ? `ICAO ${flight.hex}` : "";
  const parts = [
    icao, type, role,
    `${origin} → ${dest}`,
    [alt, km].filter(Boolean).join(", "),
    date,
  ].filter(Boolean);
  return parts.join(" | ");
}

function formatCorridorBrief(props) {
  const m = getManifest();
  const role = (props.rs || []).map(roleLabel).join(", ");
  const date = m.data_start && props.day != null
    ? dayToDateStr(props.day) : "";
  return [role, date].filter(Boolean).join(" | ");
}

function adsbeUrl(hex, date) {
  return `https://globe.adsbexchange.com/?icao=${hex}&showTrace=${date}`;
}

export function initTraceInteraction() {
  tooltipEl = document.getElementById("map-tooltip");

  let debounceTimer = null;

  map.on("mousemove", (e) => {
    updateCountryHover(e.point);
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => handleHover(e), 30);
  });

  map.on("click", (e) => {
    if (traceInteractionEnabled) {
      if (hoveredId != null) {
        const flight = lookupFlight(hoveredId);
        if (flight?.hex) {
          const m = getManifest();
          const date = m.data_start && flight.day != null
            ? dayToDateStr(flight.day) : null;
          if (date) {
            // "noopener" makes window.open() return null whether or not
            // the popup opened, so onFlightOpen cannot be gated on it; a
            // real click gesture is enough for browsers to allow the tab.
            // The first time, the user is asked before being sent to a
            // third-party site; the answer is remembered.
            confirmExternalNavigation(adsbeUrl(flight.hex, date), () => {
              window.open(adsbeUrl(flight.hex, date), "_blank", "noopener");
              onFlightOpen?.();
            });
          }
        }
        return;
      }
      // Explore is active but nothing was under the click — give feedback
      // instead of doing nothing (see app.js's flashExploreMissHint, wired
      // in via initMap's onExploreMiss).
      onExploreMiss?.();
      return;
    }
    if (countryClickEnabled && countriesLoaded) {
      // null = tapped somewhere with no selectable country (sea, off-limits);
      // app.js treats that as "deselect whatever is selected".
      onCountryClick?.(queryCountryAtPoint(e.point));
    }
  });
}

function queryCountryAtPoint(point) {
  const features = map.queryRenderedFeatures(point, { layers: ["country-fill"] });
  if (features.length === 0) return null;
  const cc = features[0].properties?.cc;
  return (cc && !offLimitsSet.has(cc)) ? cc : null;
}

// One-time confirmation before handing the reader to another
// site. Names the destination explicitly — "an external site" is not enough
// when the reader's concern is *which* site.
const EXTERNAL_ACK_KEY = "adsbe-external-ack";
const EXTERNAL_HOST = "globe.adsbexchange.com";

function externalAlreadyAcked() {
  // Unavailable storage (private mode, blocked cookies) means "not yet
  // acknowledged" rather than a thrown error — the warning simply shows
  // every time, which is the safe direction.
  try { return localStorage.getItem(EXTERNAL_ACK_KEY) === "1"; }
  catch { return false; }
}

function rememberExternalAck() {
  try { localStorage.setItem(EXTERNAL_ACK_KEY, "1"); } catch { /* ignore */ }
}

function confirmExternalNavigation(url, proceed) {
  if (externalAlreadyAcked()) { proceed(); return; }

  const overlay = document.createElement("div");
  overlay.id = "external-nav-warning";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", "external-nav-title");

  const box = document.createElement("div");
  box.className = "external-nav-box";

  const h = document.createElement("h2");
  h.id = "external-nav-title";
  h.textContent = "This opens a different website";

  const p1 = document.createElement("p");
  p1.textContent =
    `Flight details are not part of this site. Continuing opens a new tab on `
    + `${EXTERNAL_HOST}, run by someone else, with this aircraft's identifier in `
    + `the address. Their operators — not this project — will see that request.`;

  const p2 = document.createElement("p");
  p2.className = "external-nav-note";
  p2.textContent = "Asked once. After this, flight clicks open straight away.";

  const row = document.createElement("div");
  row.className = "external-nav-actions";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "external-nav-cancel";
  cancel.textContent = "Stay here";

  const go = document.createElement("button");
  go.type = "button";
  go.className = "external-nav-go";
  go.textContent = `Continue to ${EXTERNAL_HOST}`;

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
  }
  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
  }

  cancel.addEventListener("click", close);
  go.addEventListener("click", () => {
    rememberExternalAck();
    close();
    proceed();
  });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", onKey, true);

  row.append(cancel, go);
  box.append(h, p1, p2, row);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  go.focus();
}

// Returns { text, pending } — text has {{comb}}/{{dir}}/{{pos}} placeholders
// already filled in (or shown as an ellipsis while sighting data loads; see
// fillHeadlineStats in data.js, shared with the sidebar info panel). `pending`
// resolves once the real numbers are available.
function countryHeadline(cc) {
  const info = getFocusConfig()?.countries?.[cc];
  return info?.headline ? fillHeadlineStats(cc, info.headline) : null;
}

// Every country breadcrumb ends with what to DO about it. The
// headline explains what the country is; without this, nothing on the map
// says that a country is clickable at all — which a reader who skipped the
// tour has no way to discover. Separated by a blank line so it reads as its
// own instruction rather than a continuation of the analysis.
const COUNTRY_CLICK_HINT =
  "Click to see which flights and landings this country was involved in.";

function withClickHint(text) {
  return escapeHtmlWithBreaks(text)
    + '<span class="country-tip-hint">'
    + escapeHtmlWithBreaks(COUNTRY_CLICK_HINT) + "</span>";
}

/** Set the hover breadcrumb's text now, and again once pending stats resolve
 * (only if `cc` is still the hovered country and it is still shown). */
function setTooltipHeadline(cc, headline) {
  tooltipEl.innerHTML = withClickHint(headline.text);
  if (!headline.pending) return;
  headline.pending.then(() => {
    if (hoveredCountryCc !== cc || tooltipEl.style.display !== "block") return;
    const fresh = countryHeadline(cc);
    if (fresh) tooltipEl.innerHTML = withClickHint(fresh.text);
  });
}

function updateCountryHover(point) {
  if (coarsePointer) {
    if (hoveredCountryCc) {
      map.setFilter("country-hover", ["==", ["get", "cc"], ""]);
      hoveredCountryCc = null;
    }
    return;
  }
  if (overLandingsCircle) {
    if (tooltipEl && tooltipEl.style.display !== "none") {
      tooltipEl.style.display = "none";
      tooltipEl.classList.remove("country-tip");
    }
    if (hoveredCountryCc) {
      map.setFilter("country-hover", ["==", ["get", "cc"], ""]);
      hoveredCountryCc = null;
    }
    return;
  }
  if (!countriesLoaded || !countryClickEnabled) {
    if (hoveredCountryCc) {
      map.setFilter("country-hover", ["==", ["get", "cc"], ""]);
      hoveredCountryCc = null;
      if (tooltipEl) {
        tooltipEl.style.display = "none";
        tooltipEl.classList.remove("country-tip");
      }
    }
    return;
  }
  const features = map.queryRenderedFeatures(point, { layers: ["country-fill"] });
  const cc = features.length > 0 ? (features[0].properties?.cc || null) : null;
  if (cc !== hoveredCountryCc) {
    hoveredCountryCc = cc;
    if (cc && GULF_TRIGGER.has(cc)) {
      map.setPaintProperty("country-hover", "fill-color", "#e53935");
      map.setPaintProperty("country-hover", "fill-opacity", 0.45);
      map.setFilter("country-hover", ["in", ["get", "cc"], ["literal", GULF_CCS]]);

      if (!traceInteractionEnabled && !isMobile && tooltipEl) {
        const headline = countryHeadline(cc);
        if (headline) {
          setTooltipHeadline(cc, headline);
          tooltipEl.classList.add("country-tip");
          const p1 = tipPos(point);
          tooltipEl.style.left = p1.x + "px";
          tooltipEl.style.top = p1.y + "px";
          tooltipEl.style.display = "block";
        }
      }
    } else {
      map.setPaintProperty("country-hover", "fill-color", HIGHLIGHT_COLOR);
      map.setPaintProperty("country-hover", "fill-opacity", 0.25);

      const hoverCc = (cc && !offLimitsSet.has(cc)) ? cc : null;
      map.setFilter("country-hover", hoverCc ? ["==", ["get", "cc"], hoverCc] : ["==", ["get", "cc"], ""]);
      if (!traceInteractionEnabled && !isMobile && tooltipEl) {
        const headline = cc ? countryHeadline(cc) : null;
        // Fallback for a display-only country not covered by GULF_TRIGGER
        // above (none exist today — UA/BY/RU always take that branch — but a
        // future display-only addition outside the corridor set lands here):
        // a plain name, never a stats headline (there's nothing to compute).
        const displayOnlyName = !headline && cc ? getFocusConfig()?.countries?.[cc]?.name : null;
        if (headline || displayOnlyName) {
          if (headline) setTooltipHeadline(cc, headline);
          // The click hint belongs here too — a display-only country
          // is just as clickable, and this branch is the one with the least
          // text to go on.
          else tooltipEl.innerHTML = withClickHint(displayOnlyName);
          tooltipEl.classList.add("country-tip");
          const p1 = tipPos(point);
          tooltipEl.style.left = p1.x + "px";
          tooltipEl.style.top = p1.y + "px";
          tooltipEl.style.display = "block";
        } else {
          tooltipEl.style.display = "none";
          tooltipEl.classList.remove("country-tip");
        }
      }
    }
  } else if (cc && !traceInteractionEnabled && tooltipEl && tooltipEl.style.display === "block") {
    const p2 = tipPos(point);
    tooltipEl.style.left = p2.x + "px";
    tooltipEl.style.top = p2.y + "px";
  }
}


function handleHover(e) {
  if (!traceInteractionEnabled) { clearHover(); return; }
  if (!routesLoaded || map.getLayoutProperty("routes-line", "visibility") === "none") {
    clearHover();
    return;
  }

  const features = map.queryRenderedFeatures(e.point, { layers: ["routes-line"] });
  if (features.length === 0) { clearHover(); return; }

  const props = features[0].properties;
  const legId = typeof props.id === "string" ? parseInt(props.id, 10) : props.id;

  if (legId !== hoveredId) {
    hoveredId = legId;
    highlightFlight(legId);
  }

  if (tooltipEl) {
    const flight = lookupFlight(legId);
    tooltipEl.textContent = flight ? formatFlightTooltip(flight) : formatCorridorBrief(props);
    const tp = tipPos(e.point);
    tooltipEl.style.left = tp.x + "px";
    tooltipEl.style.top = tp.y + "px";
    tooltipEl.style.display = "block";
  }
  map.getCanvas().style.cursor = "pointer";
}

function clearHover() {
  if (hoveredId !== null) {
    hoveredId = null;
    if (map.getLayer("routes-line")) {
      const base = routeBasePaint();
      map.setPaintProperty("routes-line", "line-opacity", base.opacity);
      map.setPaintProperty("routes-line", "line-width", base.width);
    }
  }
  if (tooltipEl && !tooltipEl.classList.contains("country-tip")) {
    tooltipEl.style.display = "none";
  }
  map.getCanvas().style.cursor = hoveredCountryCc ? "pointer" : "";
}

function routeBasePaint() {
  const cc = currentRouteCc;
  return {
    opacity: cc
      ? ["interpolate", ["linear"], ["zoom"], 3, 0.2, 6, 0.35, 8, 0.5, 10, 0.7, 12, 0.85]
      : ["interpolate", ["linear"], ["zoom"], 3, 0.03, 6, 0.06, 8, 0.15, 10, 0.35, 12, 0.6],
    width: cc
      ? ["interpolate", ["linear"], ["zoom"], 3, 0.8, 6, 1.2, 8, 1.8, 10, 2.5, 12, 3.5]
      : ["interpolate", ["linear"], ["zoom"], 3, 1.5, 6, 2.5, 8, 3, 10, 4, 12, 5],
  };
}

let savedCorridorColor = null;
let whatIfActive = false;

export function setWhatIfCorridor(directIds, positioningIds, preDenialIds, denialDay) {
  if (!routesLoaded || !map.getLayer("routes-line")) return;
  whatIfActive = true;
  savedCorridorColor = savedCorridorColor || map.getPaintProperty("routes-line", "line-color");

  const directList = [...directIds];
  const posList = [...positioningIds];
  let colorExpr;
  if (directList.length > 0 && posList.length > 0) {
    colorExpr = ["case",
      ["in", ["get", "id"], ["literal", directList]], "#e53935",
      ["in", ["get", "id"], ["literal", posList]], "#42a5f5",
      "#4caf50"];
  } else if (directList.length > 0) {
    colorExpr = ["case",
      ["in", ["get", "id"], ["literal", directList]], "#e53935",
      "#4caf50"];
  } else if (posList.length > 0) {
    colorExpr = ["case",
      ["in", ["get", "id"], ["literal", posList]], "#42a5f5",
      "#4caf50"];
  } else {
    colorExpr = "#4caf50";
  }
  map.setPaintProperty("routes-line", "line-color", colorExpr);

  const opacity = ["interpolate", ["linear"], ["zoom"], 3, 0.06, 6, 0.12, 8, 0.3, 10, 0.5, 12, 0.7];
  map.setPaintProperty("routes-line", "line-opacity", opacity);

  // Show all post-denial flights (green corridors + red/blue affected) plus
  // pre-denial flights that crossed the country (green, visible context).
  const preDenialList = [...preDenialIds];
  const allClassified = [...new Set([...directList, ...posList, ...preDenialList])];
  const dayFilter = allClassified.length > 0
    ? ["any",
        [">=", ["get", "day"], denialDay],
        ["in", ["get", "id"], ["literal", allClassified]]]
    : [">=", ["get", "day"], denialDay];
  const parts = [...currentBaseFilter, dayFilter];
  map.setFilter("routes-line", parts);
}

export function clearWhatIfCorridor() {
  if (!whatIfActive || !routesLoaded) return;
  whatIfActive = false;
  if (savedCorridorColor) {
    map.setPaintProperty("routes-line", "line-color", savedCorridorColor);
    savedCorridorColor = null;
  }
  const base = routeBasePaint();
  map.setPaintProperty("routes-line", "line-opacity", base.opacity);
  refilterRoutes();
}

function highlightFlight(legId) {
  if (legId == null || !map.getLayer("routes-line")) return;
  const z = map.getZoom();
  const base = routeBasePaint();
  const baseOpacity = evalZoomExpr(base.opacity, z);
  const baseWidth = evalZoomExpr(base.width, z);
  const match = ["==", ["get", "id"], legId];
  map.setPaintProperty("routes-line", "line-opacity",
    ["case", match, 0.9, baseOpacity]);
  map.setPaintProperty("routes-line", "line-width",
    ["case", match, 5, baseWidth]);
}

function evalZoomExpr(expr, z) {
  if (typeof expr === "number") return expr;
  if (!Array.isArray(expr) || expr[0] !== "interpolate") return expr;
  const stops = expr.slice(3);
  if (stops.length < 4) return stops[1] ?? 0;
  for (let i = 0; i < stops.length - 2; i += 2) {
    if (z <= stops[i]) return stops[i + 1];
    if (z <= stops[i + 2]) {
      const t = (z - stops[i]) / (stops[i + 2] - stops[i]);
      return stops[i + 1] + t * (stops[i + 3] - stops[i + 1]);
    }
  }
  return stops[stops.length - 1];
}

