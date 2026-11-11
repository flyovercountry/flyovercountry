// Single source of truth for the 16-band altitude colour ramp, consumed by
// both the MapLibre line-colour expression (map.js) and the on-map legend
// gradient (app.js -> mount.js -> OnMapLegend.svelte). Previously this array
// was duplicated verbatim in both files; keeping it here means the map and
// legend can never drift out of sync.
export const ALT_COLORS = [
  "#083e7a", "#0d5ba8", "#1278c8", "#1b94d4",
  "#28aad0", "#3cbfb0", "#5dcc7e", "#8ad652",
  "#b5de30", "#d6e21a", "#ede613", "#f7d00e",
  "#f5a623", "#ef6c00", "#e53935", "#b71c1c",
];
