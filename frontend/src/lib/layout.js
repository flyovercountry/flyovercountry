/** The sidebar/sheet layout breakpoint, read from tailwind.css's @theme
 * (--breakpoint-md) so CSS and JS cannot disagree. Every layout decision
 * (which panel layout, which toolbar, which tour steps, whether rect-export
 * initialises) goes through isDesktopLayout(). The stylesheet is loaded
 * before this deferred module runs; 900 is the fallback for a context with
 * no document. */
function cssBreakpoint(name, fallback) {
  if (typeof document === "undefined") return fallback;
  const v = parseInt(getComputedStyle(document.documentElement).getPropertyValue(name), 10);
  return Number.isFinite(v) ? v : fallback;
}

export const DESKTOP_BREAKPOINT = cssBreakpoint("--breakpoint-md", 900);

/** True once the viewport is wide enough for the full sidebar layout
 * (desktop toolbar, show-toggle, rect-export, desktop tour steps). */
export function isDesktopLayout() {
  return window.innerWidth >= DESKTOP_BREAKPOINT;
}

/** A phone in landscape, where style.css's #rotate-overlay covers the page.
 * Used only by tour.js, to not start the tour under that overlay. Mirrors
 * the overlay's media query (landscape, max-height 500px, pointer coarse);
 * keep both in sync. */
export function isLandscapePhone() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  return h <= 500 && w > h && matchMedia("(pointer: coarse)").matches;
}
