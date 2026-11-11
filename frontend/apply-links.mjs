// Applies the untracked repository link (data/in/config/frontend/repo.json)
// to the built methodology page, data/out/methodology.html, which is static
// HTML with no script of its own. Runs on every `vite build`
// (vite.config.js plugin, after the static copy) and from copy-data.mjs.
//
// The source page marks the sentence with data attributes:
//   <span data-repo-sentence>The <a data-repo-link href="">source code</a> is public.</span>
// With a valid repo.json the href is filled in; without one the whole
// sentence is removed, so the page never shows a dead link.
//
// Same rules as frontend/src/lib/links.js resolveLink: `link` required,
// mailto: or https: only; `text` is unused here (the sentence has its own
// wording).
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = dirname(HERE);
const REPO_CONFIG = join(REPO_ROOT, "data", "in", "config", "frontend", "repo.json");
const METHODOLOGY_OUT = join(REPO_ROOT, "data", "out", "methodology.html");

const ALLOWED_SCHEMES = ["mailto:", "https:"];
const SENTENCE_RE = /<span data-repo-sentence>[\s\S]*?<\/span>/;
const LINK_RE = /(<a data-repo-link href=")[^"]*(")/;

export function resolveRepoLink(raw) {
  if (!raw || typeof raw !== "object") return null;
  const link = String(raw.link || "").trim();
  if (!ALLOWED_SCHEMES.some((s) => link.toLowerCase().startsWith(s))) return null;
  return link;
}

function escapeAttr(s) {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

/** Pure: the page HTML with the repo sentence resolved. `link` null removes
 * the sentence. Idempotent on an already-processed page. */
export function rewriteMethodology(html, link) {
  if (!link) return html.replace(SENTENCE_RE, "");
  return html.replace(LINK_RE, `$1${escapeAttr(link)}$2`);
}

export function readRepoLink() {
  if (!existsSync(REPO_CONFIG)) return null;
  try {
    return resolveRepoLink(JSON.parse(readFileSync(REPO_CONFIG, "utf8")));
  } catch {
    return null;
  }
}

export function applyLinks() {
  if (!existsSync(METHODOLOGY_OUT)) return;
  const html = readFileSync(METHODOLOGY_OUT, "utf8");
  const out = rewriteMethodology(html, readRepoLink());
  if (out !== html) writeFileSync(METHODOLOGY_OUT, out);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  applyLinks();
  console.log(`[apply-links] repo link -> ${METHODOLOGY_OUT}`);
}
