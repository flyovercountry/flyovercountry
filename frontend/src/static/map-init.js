// Map bootstrap. Kept as a separate classic (non-module) script rather than an
// inline <script> so the Content-Security-Policy can drop script-src
// 'unsafe-inline' (defense-in-depth against XSS). Runs after maplibre-gl loads
// and before app.js (a deferred module), publishing the shared window.__map
// that app.js and the browser test suite consume.
window.__map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      carto: {
        type: "vector",
        url: "https://tiles.basemaps.cartocdn.com/vector/carto.streets/v1/tiles.json",
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0a0a0a" } },
      {
        id: "water", type: "fill", source: "carto", "source-layer": "water",
        filter: ["==", "$type", "Polygon"],
        paint: { "fill-color": "#1c2329", "fill-antialias": true },
      },
      {
        id: "waterway", type: "line", source: "carto", "source-layer": "waterway",
        paint: {
          "line-color": "rgba(42, 58, 70, 1)",
          "line-width": { stops: [[8, 0.5], [9, 1], [15, 2], [16, 3]] },
        },
      },
      {
        id: "boundary_country_outline", type: "line", source: "carto",
        "source-layer": "boundary", minzoom: 6,
        filter: ["all", ["==", "admin_level", 2], ["==", "maritime", 0]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#2C353C", "line-opacity": 0.5, "line-width": 8 },
      },
      {
        id: "boundary_country_inner", type: "line", source: "carto",
        "source-layer": "boundary",
        filter: ["all", ["==", "admin_level", 2], ["==", "maritime", 0]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": { stops: [[4, "rgba(68,70,70,1)"], [5, "rgba(72,72,72,1)"], [6, "rgba(78,78,78,1)"]] },
          "line-width": { stops: [[3, 1], [6, 1.5]] },
        },
      },
    ],
  },
  center: [15, 45],
  zoom: 4,
  minZoom: 3,
  maxZoom: 14,
  maxBounds: [[-30, 10], [65, 72]],
  attributionControl: false,
  dragRotate: false,
  pitchWithRotate: false,
  maxPitch: 0,
});
window.__map.touchZoomRotate.disableRotation();
window.__map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
window.__map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
// Start the attribution collapsed. MapLibre opens it either synchronously
// on this tick or later when source attribution arrives, so check now and
// also observe; stop after the first correction so the user's own click is
// left alone. The [open]:not(:focus-within) CSS rule is the visual backstop.
(function() {
  var el = document.querySelector('.maplibregl-ctrl-attrib');
  if (!el) return;
  function closeIfOpen() {
    if (el.hasAttribute('open')) {
      el.removeAttribute('open');
      el.classList.remove('maplibregl-compact-show');
      return true;
    }
    return false;
  }
  if (closeIfOpen()) return;
  var obs = new MutationObserver(function() {
    closeIfOpen();
    obs.disconnect();
  });
  obs.observe(el, { attributes: true, attributeFilter: ['open'] });
})();
