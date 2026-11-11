import { createServer } from "node:http";
import { readFileSync, statSync, existsSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join, extname, resolve, sep } from "node:path";

const PORT = parseInt(process.env.PORT || "8150", 10);
const HOST = process.env.HOST || "127.0.0.1";
// The one served root. data/build/ and data/in/ are siblings of data/out/,
// not under it, so nothing resolved inside BUILD can reach them.
const BUILD = resolve(process.env.BUILD_DIR || "data/out");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".geojson": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".ttf": "font/ttf",
};

const COMPRESSIBLE = new Set([
  ".html", ".js", ".mjs", ".css", ".json", ".geojson", ".svg",
]);

// Security headers come from frontend/src/static/_headers, the file
// Cloudflare Pages reads at deploy time, so the two cannot drift. Resolved
// relative to this file, with the copy in data/out/ as the fallback when
// the source tree is not present.
const SCRIPT_DIR = new URL(".", import.meta.url).pathname;
const HEADERS_CANDIDATES = [
  join(SCRIPT_DIR, "src", "static", "_headers"),
  join(BUILD, "_headers"),
];

/** The `Header-Name: value` lines of a Cloudflare Pages `_headers` file's
 * `/*` block. This server serves every path the same way, so other
 * path-scoped blocks are ignored. Throws when the block is missing so the
 * caller can try the next candidate. */
function parseHeadersFile(path) {
  const lines = readFileSync(path, "utf8").split("\n");
  const headers = {};
  let inBlock = false;
  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) {
      inBlock = line.trim() === "/*";
      continue;
    }
    if (!inBlock) continue;
    const m = line.match(/^\s*([^:]+):\s*(.*)$/);
    if (m) headers[m[1].trim()] = m[2].trim();
  }
  if (Object.keys(headers).length === 0) {
    throw new Error(`no "/*" header block found in ${path}`);
  }
  return headers;
}

const SECURITY_HEADERS = (() => {
  for (const candidate of HEADERS_CANDIDATES) {
    try {
      return parseHeadersFile(candidate);
    } catch { /* try the next location */ }
  }
  console.error(
    `WARNING: no readable _headers found (tried: ${HEADERS_CANDIDATES.join(", ")}) — ` +
    `serving WITHOUT security headers. Fix the deployment; do not ship this state.`,
  );
  return {};
})();

const gzCache = new Map();

function getEntry(filePath) {
  const mtime = statSync(filePath).mtimeMs;
  let entry = gzCache.get(filePath);
  if (entry && entry.mtime === mtime) return entry;
  const raw = readFileSync(filePath);
  const compressed = gzipSync(raw, { level: 9 });
  entry = { raw, compressed, mtime };
  gzCache.set(filePath, entry);
  return entry;
}

// Resolve a URL path under BUILD: null when it escapes the root (`..`),
// undefined when its percent-encoding is malformed (a 400, not a crash).
function safePath(urlPath) {
  let clean;
  try {
    clean = decodeURIComponent(urlPath.split("?")[0].split("#")[0]);
  } catch {
    return undefined;
  }
  const resolved = resolve(join(BUILD, clean));
  if (resolved !== BUILD && !resolved.startsWith(BUILD + sep)) return null;
  return resolved;
}

createServer((req, res) => {
  try {
    const url = req.url || "/";
    const resolvedPath = safePath(url);
    if (resolvedPath === undefined) {
      res.writeHead(400, { "Content-Type": "text/plain" });
      res.end("Bad Request");
      return;
    }
    let filePath = resolvedPath;
    if (!filePath) {
      res.writeHead(403);
      res.end();
      return;
    }

    // A rebuild can remove a file between these checks and the read; the
    // outer catch turns that into a 404 instead of taking the server down.
    if (existsSync(filePath) && statSync(filePath).isDirectory()) {
      filePath = join(filePath, "index.html");
    }

    if (!existsSync(filePath)) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("Not Found");
      return;
    }

    const ext = extname(filePath).toLowerCase();
    const contentType = MIME[ext] || "application/octet-stream";

    for (const [k, v] of Object.entries(SECURITY_HEADERS)) {
      res.setHeader(k, v);
    }
    res.setHeader("Cache-Control", "no-cache");
    // No Access-Control-Allow-Origin: nothing fetches cross-origin from
    // this server (the embed is an iframe, governed by frame-ancestors).

    const acceptsGzip = (req.headers["accept-encoding"] || "").includes("gzip");
    if (acceptsGzip && COMPRESSIBLE.has(ext)) {
      const { raw, compressed } = getEntry(filePath);
      res.writeHead(200, {
        "Content-Type": contentType,
        "Content-Encoding": "gzip",
        "Content-Length": compressed.length,
        // data.js's download progress falls back to this when the browser
        // does not report decompressed progress on a gzipped XHR.
        "X-Decompressed-Content-Length": raw.length,
      });
      res.end(compressed);
    } else {
      const { raw } = getEntry(filePath);
      res.writeHead(200, {
        "Content-Type": contentType,
        "Content-Length": raw.length,
      });
      res.end(raw);
    }
  } catch (err) {
    if (!res.headersSent) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("Not Found");
    } else {
      res.end();
    }
  }
}).listen(PORT, HOST, () => {
  console.log(`serving ${BUILD} on http://${HOST}:${PORT}`);
});
