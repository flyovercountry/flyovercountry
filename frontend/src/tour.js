import { getFocusConfig } from "./data.js";
import { getMap, highlightCountry, clearCountryInteraction, hideLandingsForTour, restoreLandingsAfterTour } from "./map.js";
import { computeGlobalStats } from "./stats.js";
import { hasParams } from "./url.js";
import { openFilterView, closeFilterView } from "./lib/stores/panel.js";
import { isDesktopLayout, isLandscapePhone } from "./lib/layout.js";
import { setSidebarCollapsed, dismissAllTransients } from "./lib/stores/ui.js";
import { closeReader } from "./lib/stores/reader.js";
import { forceExitWhatIf } from "./whatif.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const OVERLAY_FILL = "rgba(0,0,0,0.75)";
const FADE_MS = 220;
const GLOW_COLOR = "rgba(100,180,255,0.5)";
const GLOW_BLUR = 6;
// Minimum clearance between an annotation box and the viewport edge.
const ANN_GAP = 20;

// ── Corridor highlighting ────────────────────────────────────────

let originalColor = null;
let originalOpacity = null;
let tourBorderAdded = false;

// An editorial list, not derived from permissiveness levels (level 0 alone
// misses ES/FR/BE, levels 0+1 would add Italy; TR is not a config country).
// UA/BY/RU are listed because the match expression below replaces
// fill-color outright and leaves unlisted countries transparent.
const TOUR_RESTRICTED_CCS = ["AT", "CH", "ES", "FR", "BE", "IT", "TR", "UA", "BY", "RU"];
const TOUR_RED = "#e53935";

// MapLibre transitions paint properties over ~300 ms; for a categorical
// fill-color match it holds the old colours for the whole window, which
// under this map's render load stretches past a second. A zero-duration
// transition makes the change render on the next frame.
function setPaintPropertyNow(map, layer, prop, value) {
  map.setPaintProperty(layer, `${prop}-transition`, { duration: 0, delay: 0 });
  map.setPaintProperty(layer, prop, value);
}

function highlightRefusingCountries() {
  const map = getMap();
  if (!map) return;
  const fc = getFocusConfig();
  if (!fc?.countries) return;

  const layer = "country-fill";
  if (!map.getLayer(layer)) return;

  // Clear any prior selection/hover (e.g. a country the user had selected
  // before opening the tour) so it doesn't sit on top of this step's
  // red/transparent override looking like a stray highlighted country.
  clearCountryInteraction();

  originalColor = map.getPaintProperty(layer, "fill-color");
  originalOpacity = map.getPaintProperty(layer, "fill-opacity");

  const matchExpr = ["match", ["get", "cc"]];
  for (const cc of TOUR_RESTRICTED_CCS) matchExpr.push(cc, TOUR_RED);
  matchExpr.push("rgba(0,0,0,0)");

  setPaintPropertyNow(map, layer, "fill-color", matchExpr);
  setPaintPropertyNow(map, layer, "fill-opacity", 0.55);

  if (!map.getLayer("country-tour-border")) {
    map.addLayer({
      id: "country-tour-border",
      type: "line",
      source: "countries",
      paint: {
        "line-color": "rgba(255,255,255,0.5)",
        "line-width": 1.5,
      },
    });
    tourBorderAdded = true;
  }

  setLegendHidden(true);
}

function restoreCountryColors() {
  setLegendHidden(false);

  const map = getMap();
  if (!map || originalColor == null) return;
  const layer = "country-fill";
  if (!map.getLayer(layer)) return;
  setPaintPropertyNow(map, layer, "fill-color", originalColor);
  setPaintPropertyNow(map, layer, "fill-opacity", originalOpacity);
  originalColor = null;
  originalOpacity = null;
  if (tourBorderAdded && map.getLayer("country-tour-border")) {
    map.removeLayer("country-tour-border");
    tourBorderAdded = false;
  }
}

// The tier legend shows country-color categories that don't match the
// red-vs-transparent override used during the corridor step, so it's
// hidden for the duration of that step and restored on exit.
function setLegendHidden(hidden) {
  const onMapLegendMount = document.getElementById("onmap-legend-mount");
  if (onMapLegendMount) onMapLegendMount.style.display = hidden ? "none" : "";
}

// ── Step definitions ─────────────────────────────────────────────

function desktopSteps() {
  const gs = computeGlobalStats();
  const days = gs?.dates?.filter(d => d).length || 0;
  const fc = getFocusConfig();
  const title = fc?.title || "Flyover Country";
  const red = TOUR_RED;

  return [
    {
      highlights: [],
      annotations: [
        {
          html: `<h3>${title}</h3>
            <p style="font-size:15px;line-height:1.5">The US-Israeli war on Iran and Lebanon is completely reliant on military flights
            across European airspace. This map shows
            <strong>thousands of flights over the course of ${days > 0 ? days : "50"} days of the war</strong>
            &mdash; where they go, which countries enable them, and what would change
            if any single country said no.</p>
            <p class="tour-aside">In the US, &ldquo;flyover country&rdquo; is a derogatory
            phrase for states that wealthier Americans fly over but don&rsquo;t think about.
            Is your country a flyover country?</p>`,
          place: "map-top-right",
          hasNav: true,
        },
        {
          html: `<p><strong>Flight data</strong> is computed from publicly available aircraft
            transponder records (ADS-B, MLAT) &mdash; no manual editing, no AI-generated data.
            The code and documentation of the data pipeline is published for review.</p>
            <p>Additional text on legal treaties and history per country is AI-assisted
            research material with cited sources and its accuracy is not guaranteed.
            Corrections are welcomed.</p>`,
          place: "map-bottom-right",
          secondary: true,
        },
      ],
      onEnter() { setLegendHidden(true); },
      onExit() { setLegendHidden(false); },
    },
    {
      highlights: [
        { area: "map" },
      ],
      annotations: [
        {
          html: `<h3>A narrow corridor</h3>
            <p>Countries in <strong style="color:${red}">red</strong>
            have restricted US military overflight. In the east, ongoing war
            closes that airspace. In the west, governments have objected to
            the US war and refused access. What remains is a <strong>narrow
            corridor</strong> through central Europe.</p>
            <p>A single country along this corridor could disrupt the logistics
            on its own. A unified European response is not required.</p>`,
          place: "map-top-right",
          hasNav: true,
        },
      ],
      onEnter: highlightRefusingCountries,
      onExit: restoreCountryColors,
    },
    // The Show toggle offers Flights and Landings with no
    // statement of what the choice means, and the difference carries the
    // site's central argument. Highlighting the toggle itself rather than
    // the map, so the control being explained is the thing lit up.
    {
      highlights: [
        { sel: "#show-subsection", padding: 10, glow: true },
        { sel: "#sidebar", padding: 0 },
      ],
      annotations: [
        {
          html: `<h3>Flyover matters as much as landings</h3>
            <p>Attention tends to focus on <strong>bases and landings</strong>, which are
            concentrated in a few countries with very strong US agreements. But
            <strong>flyover permission is just as vital</strong> &mdash; many more countries
            take part simply by letting the traffic pass, and any one refusing could be
            substantially disruptive on its own.</p>`,
          place: "map-top-right",
          hasNav: true,
        },
        {
          html: `<p style="font-size:12px;line-height:1.45"><strong>A limit worth knowing.</strong>
            The legal research on this site covers <strong>flyover permission only</strong>.
            Landing and basing rights were not researched: they are more site-specific, and
            the agreements behind them are harder to interpret reliably. The landing
            <em>data</em> is computed the same way as everything else &mdash; but it does not
            come with the same legal analysis behind it.</p>`,
          place: "map-bottom-right",
          secondary: true,
        },
      ],
      onEnter() { setLegendHidden(true); },
      onExit() { setLegendHidden(false); },
    },
    {
      highlights: [
        { sel: "#sidebar", padding: 0 },
        { sel: "#share-menu-trigger", padding: 6, glow: true },
        { mapCoord: [10.5, 51.2], size: 240, glow: true },
      ],
      annotations: [
        {
          html: `<p style="font-size:14px;line-height:1.5"><strong>Share</strong> &mdash; copy a link, embed, or
            post to social media. Your current filters are preserved
            in the link.</p>`,
          near: "#share-menu-trigger",
          side: "right",
          top: 14,
          secondary: true,
        },
        {
          html: `<p style="font-size:15px;line-height:1.5"><strong>Country details</strong> &mdash;
            click on a country to see how many flights cross it and which types of
            aircraft use its airspace. Click on the &ldquo;Refuse?&rdquo; button to see what
            could change if that country refused overflight.
            Click on a country again to deselect it.</p>`,
          place: "map-center",
          hasNav: true,
        },
        {
          html: `<p style="font-size:12px;line-height:1.4"><strong>Filter and explore</strong> &mdash;
            the filter panel (open now on the left) lets you narrow
            the data by role, time range, and theater connection.
            The methodology page explains the exact definitions.</p>`,
          near: "#sidebar",
          side: "right",
          top: 320,
          secondary: true,
        },
      ],
      onEnter() {
        openFilterView();
        highlightCountry("DE");
      },
      onExit() {
        closeFilterView();
        highlightCountry(null);
      },
    },
  ];
}

function mobileSteps() {
  const gs = computeGlobalStats();
  const days = gs?.dates?.filter(d => d).length || 0;
  const fc = getFocusConfig();
  const title = fc?.title || "Flyover Country";
  const red = TOUR_RED;

  return [
    {
      highlights: [],
      annotations: [
        {
          html: `<h3>${title}</h3>
            <p>The US-Israeli war on Iran and Lebanon is completely reliant on military flights
            across European airspace. This map shows
            <strong>thousands of flights over the course of ${days > 0 ? days : "50"} days of the war</strong>
            &mdash; where they go, which countries enable them, and what would change
            if any single country said no.</p>
            <p class="tour-aside"><em>Flight data</em> is computed from public transponder
            records &mdash; no AI-generated data. The code and documentation of the data pipeline
            is published for review. Additional country text is AI-assisted research with cited
            sources; accuracy is not guaranteed.</p>`,
          place: "center",
          hasNav: true,
        },
      ],
    },
    {
      highlights: [
        { area: "map" },
      ],
      annotations: [
        {
          html: `<h3>A narrow corridor</h3>
            <p>Countries in <strong style="color:${red}">red</strong>
            have restricted US military overflight. In the east, ongoing war
            closes that airspace. In the west, governments have objected to
            the US war and refused access. What remains is a <strong>narrow
            corridor</strong> through central Europe.</p>
            <p>A single country along this corridor could disrupt the logistics
            on its own. A unified European response is not required.</p>`,
          place: "center",
          hasNav: true,
        },
      ],
      onEnter: highlightRefusingCountries,
      onExit: restoreCountryColors,
    },
    // The same stage on mobile. The two step lists are separate,
    // so a step added to only one reaches half the users. Here the Show
    // toggle lives in .mobile-toolbar, not the desktop toolbar group.
    {
      highlights: [
        { sel: ".mobile-toolbar #show-subsection", padding: 10, glow: true },
      ],
      annotations: [
        {
          html: `<h3>Flyover matters as much as landings</h3>
            <p>Attention tends to focus on <strong>bases and landings</strong>, concentrated
            in a few countries with very strong US agreements. But <strong>flyover permission
            is just as vital</strong> &mdash; many more countries take part just by letting
            traffic pass, and any one refusing could be substantially disruptive on its
            own.</p>
            <p class="tour-aside">The legal research on this site covers <strong>flyover
            permission only</strong>. Landing and basing rights were not researched &mdash;
            they are more site-specific and their agreements are harder to interpret
            reliably.</p>`,
          place: "center",
          hasNav: true,
        },
      ],
    },
    {
      highlights: [
        { sel: "#mobile-filter-btn", padding: 12, glow: true },
      ],
      annotations: [
        {
          html: `<h3>Explore</h3>
            <p><strong>Tap any country</strong> to see how many flights cross
            it and which types of aircraft use its airspace. Tap the
            &ldquo;Refuse?&rdquo; button to see what could change if that
            country refused overflight.</p>
            <p style="font-size:13px"><strong>Filter</strong> by role, time range, and theater
            connection using the filter button. <strong>Share</strong> a link
            from the toolbar &mdash; your filters are preserved.</p>`,
          place: "center",
          hasNav: true,
        },
      ],
    },
  ];
}

// ── Tour engine ──────────────────────────────────────────────────

let overlay = null;
let maskEl = null;
let svgRoot = null;
let annContainer = null;
let steps = null;
let stepIdx = 0;
let active = false;
let boundKeyHandler = null;
let boundResizeHandler = null;
let cutoutEls = [];
let glowEls = [];
const BLUR_SM = 12;
const BLUR_LG = 40;

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

function buildOverlay() {
  overlay = document.createElement("div");
  overlay.id = "tour-overlay";

  const svg = svgEl("svg", {
    width: "100%", height: "100%",
    style: "position:absolute;inset:0",
  });
  svgRoot = svg;

  const defs = svgEl("defs");

  maskEl = svgEl("mask", {
    id: "tour-mask",
    maskUnits: "userSpaceOnUse",
    x: "0", y: "0", width: "100%", height: "100%",
  });
  maskEl.appendChild(svgEl("rect", {
    width: "100%", height: "100%", fill: "white",
  }));
  defs.appendChild(maskEl);

  const filter = svgEl("filter", {
    id: "tour-glow", x: "-50%", y: "-50%", width: "200%", height: "200%",
  });
  const blur = svgEl("feGaussianBlur", {
    stdDeviation: String(GLOW_BLUR), result: "blur",
  });
  const merge = svgEl("feMerge");
  merge.appendChild(svgEl("feMergeNode", { in: "blur" }));
  merge.appendChild(svgEl("feMergeNode", { in: "SourceGraphic" }));
  filter.appendChild(blur);
  filter.appendChild(merge);
  defs.appendChild(filter);

  const blurSm = svgEl("filter", {
    id: "tour-mask-blur-sm", x: "-15%", y: "-15%", width: "130%", height: "130%",
  });
  blurSm.appendChild(svgEl("feGaussianBlur", { stdDeviation: String(BLUR_SM) }));
  defs.appendChild(blurSm);

  const blurLg = svgEl("filter", {
    id: "tour-mask-blur-lg", x: "-30%", y: "-30%", width: "160%", height: "160%",
  });
  blurLg.appendChild(svgEl("feGaussianBlur", { stdDeviation: String(BLUR_LG) }));
  defs.appendChild(blurLg);

  svg.appendChild(defs);

  svg.appendChild(svgEl("rect", {
    width: "100%", height: "100%",
    fill: OVERLAY_FILL, mask: "url(#tour-mask)",
  }));

  overlay.appendChild(svg);

  const skipBtn = document.createElement("button");
  skipBtn.className = "tour-skip-top tour-close";
  skipBtn.textContent = "Skip tour";
  skipBtn.onclick = destroy;
  overlay.appendChild(skipBtn);

  annContainer = document.createElement("div");
  annContainer.className = "tour-annotations";
  overlay.appendChild(annContainer);

  document.body.appendChild(overlay);
}

function clearHighlights() {
  for (const el of cutoutEls) el.remove();
  cutoutEls = [];
  for (const el of glowEls) el.remove();
  glowEls = [];
}

function addCutout(r, padding, blurSize) {
  const p = padding ?? 8;
  const filterId = blurSize === "lg" ? "tour-mask-blur-lg" : "tour-mask-blur-sm";
  const rect = svgEl("rect", {
    fill: "black", rx: "8",
    filter: `url(#${filterId})`,
    x: r.left - p, y: r.top - p,
    width: r.width + p * 2, height: r.height + p * 2,
  });
  maskEl.appendChild(rect);
  cutoutEls.push(rect);
}

function addGlow(r, padding) {
  const p = padding ?? 8;
  const rect = svgEl("rect", {
    fill: "none",
    stroke: GLOW_COLOR,
    "stroke-width": "2",
    rx: "8",
    filter: "url(#tour-glow)",
    x: r.left - p, y: r.top - p,
    width: r.width + p * 2, height: r.height + p * 2,
  });
  svgRoot.appendChild(rect);
  glowEls.push(rect);
}

function setHighlights(step) {
  clearHighlights();

  for (const h of (step.highlights || [])) {
    if (h.area === "map") {
      const sw = isDesktopLayout() ? sidebarWidth() : 0;
      const r = { left: sw, top: 0, width: window.innerWidth - sw, height: window.innerHeight };
      addCutout(r, h.padding ?? 0, "lg");
      continue;
    }
    if (h.mapCoord) {
      const map = window.__map;
      if (!map) continue;
      const cr = map.getContainer().getBoundingClientRect();
      const pt = map.project(h.mapCoord);
      const sz = h.size || 160;
      const r = { left: cr.left + pt.x - sz / 2, top: cr.top + pt.y - sz / 2, width: sz, height: sz };
      addCutout(r, 0, "lg");
      if (h.glow) addGlow(r, 0);
      continue;
    }
    const el = document.querySelector(h.sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    addCutout(r, h.padding, h.blur);
    if (h.glow) addGlow(r, h.padding);
  }

  for (const g of (step.glows || [])) {
    const el = document.querySelector(g.sel);
    if (!el) continue;
    addGlow(el.getBoundingClientRect(), g.padding);
  }
}

function sidebarWidth() {
  return parseInt(
    getComputedStyle(document.documentElement)
      .getPropertyValue("--sidebar-width")
  ) || 416;
}

// Desktop annotations placed in a region of the map (right of the sidebar):
// `x` is the fraction of the map width for the box's centre, `maxW` the
// [cap, fraction of map width] for its width, `anchor` how it is pinned
// vertically.
const MAP_REGIONS = {
  "map-top-right":    { x: 0.78, maxW: [420, 0.42], anchor: "top" },
  "map-upper-left":   { x: 0.50, maxW: [420, 0.42], anchor: "top" },
  "map-lower-left":   { x: 0.38, maxW: [420, 0.42], anchor: "lower" },
  "map-center":       { x: 0.50, maxW: [440, 0.50], anchor: "middle" },
  "map-bottom-right": { x: 0.72, maxW: [420, 0.42], anchor: "bottom" },
};

function placeAnnotation(div, ann) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let css;
  if (ann.near) css = cssNearElement(ann, vw, vh);
  else if (ann.mapCoord) css = cssAtMapCoord(ann, vw, vh);
  else if (ann.place === "sidebar-bottom") css = cssSidebarBottom();
  else if (ann.place === "center") css = cssCentered(vh);
  else if (MAP_REGIONS[ann.place]) css = cssInMapRegion(MAP_REGIONS[ann.place], vw, vh);
  if (css) div.style.cssText = css;
}

function cssNearElement(ann, vw, vh) {
  const target = document.querySelector(ann.near);
  if (!target) return null;
  const gap = ANN_GAP;
  const r = target.getBoundingClientRect();
  const side = ann.side || "right";
  const maxW = ann.secondary ? 260 : 320;
  if (side === "right") {
    let left = r.right + gap;
    if (left + maxW > vw - gap) left = vw - gap - maxW;
    const top = ann.top != null ? ann.top : Math.max(gap * 3, r.top);
    return `position:absolute;left:${left}px;top:${top}px;max-width:${maxW}px;`;
  }
  if (side === "below") {
    let left = r.left + r.width / 2;
    const minLeft = sidebarWidth() + gap + maxW / 2;
    if (left < minLeft) left = minLeft;
    if (left + maxW / 2 > vw - gap) left = vw - gap - maxW / 2;
    return `position:absolute;left:${left}px;top:${r.bottom + gap}px;` +
      `transform:translateX(-50%);max-width:${maxW}px;`;
  }
  if (side === "above") {
    const left = Math.max(gap, r.left);
    return `position:absolute;left:${left}px;bottom:${vh - r.top + gap}px;max-width:${maxW}px;`;
  }
  return null;
}

function cssAtMapCoord(ann, vw, vh) {
  const map = window.__map;
  if (!map) return null;
  const gap = ANN_GAP;
  const cr = map.getContainer().getBoundingClientRect();
  const pt = map.project(ann.mapCoord);
  const maxW = ann.secondary ? 260 : 320;
  const side = ann.side || "right";
  let left = side === "right" ? cr.left + pt.x + gap : cr.left + pt.x;
  let top = side === "below" ? cr.top + pt.y + gap : cr.top + pt.y;
  if (left + maxW > vw - gap) left = vw - gap - maxW;
  if (left < gap) left = gap;
  if (top + 100 > vh) top = vh - 120;
  const xform = side === "below" ? "transform:translateX(-50%);" : "";
  return `position:absolute;left:${left}px;top:${top}px;max-width:${maxW}px;${xform}`;
}

function cssSidebarBottom() {
  const gap = ANN_GAP;
  return `position:absolute;bottom:${gap * 4}px;left:${gap}px;max-width:${sidebarWidth() - gap * 2}px;`;
}

function cssInMapRegion(region, vw, vh) {
  const gap = ANN_GAP;
  const sw = sidebarWidth();
  const mapW = vw - sw;
  const cx = sw + mapW * region.x;
  const maxW = Math.min(region.maxW[0], mapW * region.maxW[1]);
  const vertical = {
    top: `top:${gap * 3}px;transform:translateX(-50%);`,
    lower: `top:${vh * 0.55}px;transform:translateX(-50%);`,
    middle: `top:50%;transform:translate(-50%,-50%);`,
    bottom: `bottom:${gap * 4}px;transform:translateX(-50%);`,
  }[region.anchor];
  return `position:absolute;left:${cx}px;${vertical}max-width:${maxW}px;`;
}

// Phone steps. Height-capped from the live viewport (not 100vh, which on
// iOS can be taller than what is visible) and anchored from the top;
// renderStep() re-centres it once its real height is known. left+right
// rather than left:50% + translate: a box sized from one edge only gets
// the space between that edge and the far side as its available width.
function cssCentered(vh) {
  const gap = ANN_GAP;
  return `position:absolute;top:${gap}px;left:24px;right:24px;margin:0 auto;` +
    `max-width:420px;max-height:${vh - gap * 2}px;`;
}

// Top edge of the highest DOM element this step highlights (Infinity when
// it highlights none, or only map areas/coordinates).
function highlightTop(step) {
  let top = Infinity;
  for (const h of (step.highlights || [])) {
    if (!h.sel) continue;
    const el = document.querySelector(h.sel);
    if (el) top = Math.min(top, el.getBoundingClientRect().top - (h.padding || 0));
  }
  return top;
}

// Keeps a centre-placed box inside the viewport and, when there is room,
// above the control the step is pointing at — on a short phone the box
// would otherwise sit on top of the very thing it is describing.
const MIN_BOX_H = 240;

// Device safe-area inset in px, via the --sai-* custom properties style.css
// defines from env(); 0 wherever there is none.
function safeInset(side) {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(`--sai-${side}`));
  return Number.isFinite(v) ? v : 0;
}

function centerVertically(div, step) {
  const vh = window.innerHeight;
  // A box pushed to the top must clear the "Skip tour" pill (which itself
  // sits below the notch), not just the viewport edge; at the bottom it
  // must clear the home indicator.
  const skip = overlay?.querySelector(".tour-skip-top");
  const ceiling = skip ? Math.max(ANN_GAP, skip.getBoundingClientRect().bottom + 8) : ANN_GAP;
  let floor = vh - Math.max(ANN_GAP, safeInset("bottom") + 8);
  const hlTop = highlightTop(step);
  if (hlTop - ANN_GAP - ceiling >= MIN_BOX_H) floor = Math.min(floor, hlTop - ANN_GAP);
  div.style.maxHeight = `${floor - ceiling}px`;
  const top = ceiling + Math.round((floor - ceiling - div.offsetHeight) * 0.45);
  div.style.top = `${Math.max(ceiling, top)}px`;
}

// Flags the box while its text can scroll further, so the CSS can show a
// fade above the pinned nav — a borderless block of text gives no other
// hint that more of it exists below.
function trackScrollable(div) {
  const body = div.querySelector(".tour-body");
  if (!body) return;
  const update = () => {
    const remaining = body.scrollHeight - body.clientHeight - body.scrollTop;
    div.classList.toggle("tour-scrollable", remaining > 1);
  };
  body.addEventListener("scroll", update, { passive: true });
  update();
}

function buildNav(index) {
  const nav = document.createElement("div");
  nav.className = "tour-nav";

  const backBtn = document.createElement("button");
  backBtn.className = "tour-btn tour-btn-back";
  backBtn.textContent = "Back";
  backBtn.onclick = prev;
  if (index === 0) backBtn.style.visibility = "hidden";

  const progress = document.createElement("span");
  progress.className = "tour-progress";
  progress.textContent = `${index + 1} / ${steps.length}`;

  const nextBtn = document.createElement("button");
  nextBtn.className = "tour-btn tour-btn-next";
  nextBtn.textContent = index === steps.length - 1 ? "Explore the map" : "Next";
  nextBtn.onclick = next;

  nav.appendChild(backBtn);
  nav.appendChild(progress);
  nav.appendChild(nextBtn);
  return nav;
}

// A secondary (informational) box must never sit on top of the box that
// carries Back/Next — on a 900px-tall desktop window step 1's two boxes
// overlapped and the secondary intercepted clicks on Next. Where they
// intersect, the secondary moves below the nav box (and scrolls if the
// remaining height is short); the nav box is also painted above the rest.
function keepNavClear(navDiv, others) {
  if (!navDiv) return;
  navDiv.style.zIndex = "11";
  const n = navDiv.getBoundingClientRect();
  for (const o of others) {
    const r = o.getBoundingClientRect();
    const overlaps = r.left < n.right && r.right > n.left && r.top < n.bottom && r.bottom > n.top;
    if (!overlaps) continue;
    const top = Math.round(n.bottom + 12);
    o.style.bottom = "auto";
    o.style.top = `${top}px`;
    o.style.maxHeight = `${Math.max(80, window.innerHeight - top - ANN_GAP)}px`;
  }
}

function renderStep(index) {
  const step = steps[index];

  setHighlights(step);

  annContainer.innerHTML = "";
  let navDiv = null;
  const others = [];
  for (const ann of step.annotations) {
    const div = document.createElement("div");
    div.className = "tour-annotation";
    if (ann.secondary) div.classList.add("tour-secondary");
    // Text scrolls inside .tour-body; the nav stays outside it so Back/Next
    // are always on screen no matter how tall the step's text is.
    const body = document.createElement("div");
    body.className = "tour-body";
    body.innerHTML = ann.html;
    div.appendChild(body);
    if (ann.hasNav) div.appendChild(buildNav(index));
    placeAnnotation(div, ann);
    annContainer.appendChild(div);
    if (ann.place === "center") centerVertically(div, step);
    trackScrollable(div);
    if (ann.hasNav) navDiv = div; else others.push(div);
  }
  keepNavClear(navDiv, others);
  for (const o of others) trackScrollable(o);
}

function goTo(index) {
  if (index < 0 || index >= steps.length) return;

  if (active && index !== stepIdx) {
    if (steps[stepIdx]?.onExit) steps[stepIdx].onExit();
  }

  const needFade = active;
  active = true;
  stepIdx = index;

  if (steps[index].onEnter) steps[index].onEnter();

  if (needFade) {
    annContainer.style.opacity = "0";
    setTimeout(() => {
      renderStep(index);
      annContainer.style.opacity = "1";
    }, FADE_MS);
  } else {
    annContainer.style.opacity = "0";
    renderStep(index);
    requestAnimationFrame(() => { annContainer.style.opacity = "1"; });
  }
}

function next() {
  if (stepIdx < steps.length - 1) goTo(stepIdx + 1);
  else destroy();
}

function prev() {
  if (stepIdx > 0) goTo(stepIdx - 1);
}

function onKey(e) {
  if (e.key === "Escape") destroy();
  else if (e.key === "ArrowRight" || e.key === "Enter") next();
  else if (e.key === "ArrowLeft") prev();
}

function onResize() {
  renderStep(stepIdx);
}

function destroy() {
  if (steps?.[stepIdx]?.onExit) steps[stepIdx].onExit();
  restoreCountryColors();
  restoreLandingsAfterTour();

  if (overlay) { overlay.remove(); overlay = null; }
  if (boundKeyHandler) {
    document.removeEventListener("keydown", boundKeyHandler);
    boundKeyHandler = null;
  }
  if (boundResizeHandler) {
    window.removeEventListener("resize", boundResizeHandler);
    boundResizeHandler = null;
  }

  cutoutEls = [];
  glowEls = [];
  maskEl = null;
  svgRoot = null;
  annContainer = null;
  active = false;
  steps = null;
  localStorage.setItem("welcomed", "1");
}

// ── Public API ───────────────────────────────────────────────────

// On a landscape phone #rotate-overlay covers the page, so there is nothing
// to point at.
export function startTour() {
  if (isLandscapePhone()) return false;
  if (overlay) destroy();

  // The tour narrates the default map, so everything open over it (the
  // folded sidebar, filter view, reader, What-If, dropdowns, the mobile
  // sheet) is put away first.
  setSidebarCollapsed(false);
  closeFilterView();
  closeReader();
  forceExitWhatIf();
  dismissAllTransients();

  const map = getMap();
  if (map) map.jumpTo({ center: [15, 45], zoom: 4 });
  clearCountryInteraction();
  hideLandingsForTour();

  steps = isDesktopLayout() ? desktopSteps() : mobileSteps();
  stepIdx = 0;
  active = false;

  buildOverlay();

  boundKeyHandler = onKey;
  document.addEventListener("keydown", boundKeyHandler);
  boundResizeHandler = onResize;
  window.addEventListener("resize", boundResizeHandler);

  goTo(0);
  return true;
}

export function showTourOrWelcome(maybeShowTapHint) {
  if (localStorage.getItem("welcomed")) {
    maybeShowTapHint();
    return;
  }

  // Landscape phone: leave "welcomed" unset so the tour still runs on the
  // next load in portrait, rather than being silently consumed here.
  if (isLandscapePhone()) {
    maybeShowTapHint();
    return;
  }

  if (hasParams()) {
    showTourChoice(maybeShowTapHint);
    return;
  }

  const overlay = document.getElementById("welcome-overlay");
  if (overlay) overlay.remove();
  startTour();
}

function showTourChoice(maybeShowTapHint) {
  const overlay = document.getElementById("welcome-overlay");
  const box = document.getElementById("welcome-box");
  if (!overlay || !box) {
    maybeShowTapHint();
    return;
  }

  box.innerHTML = `
    <h2>Welcome</h2>
    <p class="welcome-body">You followed a link to a specific view. Would you like a quick tour first?</p>
    <div class="tour-choice-buttons">
      <button id="tour-choice-tour" class="tour-choice-btn">Take the tour</button>
      <button id="tour-choice-skip" class="tour-choice-btn tour-choice-secondary">Go to the data</button>
    </div>
  `;
  overlay.classList.remove("hidden");

  document.getElementById("tour-choice-tour").addEventListener("click", () => {
    overlay.remove();
    startTour();
  });
  document.getElementById("tour-choice-skip").addEventListener("click", () => {
    overlay.remove();
    localStorage.setItem("welcomed", "1");
    maybeShowTapHint();
  });
}
