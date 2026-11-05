/** Shared HTML escaping for strings interpolated into markup.
 *
 * Escapes all five significant characters so the result is safe in both
 * element content and quoted attribute values. This is the ONE escape
 * helper — do not add per-module copies. */
export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Same escaping, plus newline-to-<br> conversion (single newline -> <br>,
 * a blank line -> <br><br> so a paragraph break stays visible). For text
 * that only ever lands in element content, never in an attribute — used by
 * map.js's hover tooltips and breadcrumb.js's readouts, both of which need
 * a multi-line source string to render as visible line breaks. */
export function escapeHtmlWithBreaks(s) {
  return escapeHtml(s).replace(/\n\n/g, "<br><br>").replace(/\n/g, "<br>");
}
