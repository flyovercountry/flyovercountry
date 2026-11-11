// Single-sourced share context for the combined Share menu.
//
// Social buttons are handled by the VENDORED sharer.js (vendor/sharer.min.js):
// it binds every [data-sharer] node and, at CLICK time, reads their
// data-url / data-title / data-hashtags attributes to build a platform
// share-intent URL and window.open it. This module's job is therefore only:
//   1. shareContext()        — derive {url, text, hashtags} for the current view;
//   2. updateShareTargets()  — push that context onto the [data-sharer] buttons
//                              (and expose it for the custom Mastodon + native
//                              handlers) BEFORE the menu opens / when it changes.
//
// CSP-safe: no inline scripts, no external/CDN scripts, no third-party trackers.

import { getCountry } from "./filters.js";
import {
  getFocusConfig, computeWhatIf, currentDenialDay,
  getSightingIndex, loadSightingIndex, countryEntry,
} from "./data.js";

const FALLBACK_SITE_TITLE = "Flyover Country";

/** The site title from config, falling back to the literal default — single
 * source shared by the native-share dialog's title and the email share
 * target's subject line. */
function siteTitle() {
  return getFocusConfig()?.title || FALLBACK_SITE_TITLE;
}

// Fallback used only if the config's hashtags block is unavailable.
const FALLBACK_TAG = "flyovercountry";

/** Hashtags from the config templates (common.texts.json hashtags:
 * { no_country: [...], country: [...] }). "CTAGS" expands to one tag per
 * country short name (e.g. DE -> Germany, Deutschland), "CC" to the ISO
 * code. Order preserved, deduped. */
function buildHashtags(cc, fc) {
  const cfg = fc?.hashtags;
  if (!cfg) return cc ? [FALLBACK_TAG, FALLBACK_TAG + cc] : [FALLBACK_TAG];
  const templates = cc ? (cfg.country || [FALLBACK_TAG]) : (cfg.no_country || [FALLBACK_TAG]);
  const ctags = countryEntry(cc, fc)?.ctags || [];
  const out = [];
  for (const tpl of templates) {
    if (tpl.includes("CTAGS")) {
      for (const tag of ctags) out.push(tpl.replace("CTAGS", tag));
    } else if (tpl.includes("CC")) {
      if (cc) out.push(tpl.replace("CC", cc));
    } else {
      out.push(tpl);
    }
  }
  return [...new Set(out)];
}

const FALLBACK_TEXT =
  "Which countries let US military flights cross their airspace in the war on Iran? See the map:";

function hasNonDefaultParams() {
  const p = new URLSearchParams(window.location.search);
  return p.has("start") || p.has("end") || p.has("roles") || p.has("fm");
}

function buildShareText(cc, fc, nonDefault) {
  const tpl = fc?.share;
  if (!cc || !tpl?.country) return tpl?.no_country || FALLBACK_TEXT;

  const info = countryEntry(cc, fc);
  if (!info?.adjective) return tpl.no_country || FALLBACK_TEXT;

  if (!getSightingIndex(cc)) {
    loadSightingIndex(cc).then(deferredShareUpdate);
    return tpl.no_country || FALLBACK_TEXT;
  }

  const wi = computeWhatIf(cc, currentDenialDay());
  if (!wi || !wi.postTotal) return tpl.no_country || FALLBACK_TEXT;

  const pct = Math.round((wi.direct + wi.positioning) / wi.postTotal * 100);

  let text = tpl.country
    .replace("{pct}", String(pct))
    .replace("{adjective}", info.adjective)
    .replace("{name}", info.headline_name || info.name);

  if (nonDefault) text = text.replace("%", "%*");
  return text;
}

/** Build the share context for the current view.
 * @param {function} [syncUrl] optional callback to flush current view state
 *   into window.location before we read the URL (matches #share-btn behaviour).
 * @returns {{url:string, text:string, hashtags:string[]}}
 */
export function shareContext(syncUrl) {
  if (typeof syncUrl === "function") syncUrl();
  const url = window.location.href;
  const cc = getCountry();
  const fc = getFocusConfig();
  const nonDefault = hasNonDefaultParams();
  return {
    url,
    text: buildShareText(cc, fc, nonDefault),
    hashtags: buildHashtags(cc, fc),
    urlNote: nonDefault ? "*See details:" : "",
  };
}

/** Extract a bare host from free-form Mastodon instance input.
 * Accepts "mastodon.social", "https://mastodon.social/", "@user@host" tails,
 * etc. Returns "" if nothing host-like remains. */
export function sanitizeInstance(raw) {
  if (!raw) return "";
  let s = String(raw).trim();
  s = s.replace(/^https?:\/\//i, "");   // drop scheme
  s = s.replace(/^@[^@]*@/, "");         // drop a leading "@user@" tail
  s = s.split(/[/?#]/)[0];               // host only, no path/query/fragment
  s = s.replace(/^@+/, "");              // stray leading @
  // A valid host: labels of [a-z0-9-] separated by dots, at least one dot.
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i.test(s)) return "";
  return s.toLowerCase();
}

function deferredShareUpdate() { updateShareTargets(); }

/** Refresh every social button's sharer.js data-attributes from the current
 * view, and return the same context so callers can drive the custom Mastodon
 * and native-share handlers off one source of truth.
 *
 * sharer.js reads data-* at CLICK time, so this only needs to run before the
 * menu is used: call it on the Share menu's open() and on view/country change.
 *
 * @param {function} [syncUrl] flushes view state into the URL (see shareContext).
 * @returns {{url:string, text:string, hashtags:string[]}}
 */
export function updateShareTargets(syncUrl) {
  const ctx = shareContext(syncUrl);
  const csvHashtags = ctx.hashtags.join(",");   // "flyovercountry,flyovercountryES"
  const title = ctx.urlNote ? ctx.text + "\n" + ctx.urlNote : ctx.text;

  document.querySelectorAll("#share-menu-list [data-sharer]").forEach((btn) => {
    btn.setAttribute("data-url", ctx.url);
    btn.setAttribute("data-title", title);
    btn.setAttribute("data-hashtags", csvHashtags);
    // email (sharer platform "email") builds subject from data-subject; leave a
    // stable, neutral subject and let title/url flow into the body.
    if (btn.dataset.sharer === "email") btn.setAttribute("data-subject", siteTitle());
  });

  return ctx;
}

export { siteTitle };
