// Copies the git-tracked data/in/config/frontend/ JSONs into data/out/data/
// on every `vite build` (vite.config.js plugin), so the site's config is
// present without running the pipeline. Never wipes the destination:
// `./run etl export` writes the rest of data/out/data/. The dev watcher
// only tracks frontend/src, so after a config-only edit run this directly:
//   node frontend/copy-data.mjs
import { existsSync, mkdirSync, readdirSync, copyFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = dirname(HERE);
const CONFIG_DIR = join(REPO_ROOT, "data", "in", "config", "frontend");
const OUT_DATA_DIR = join(REPO_ROOT, "data", "out", "data");

export function copyData() {
  if (!existsSync(CONFIG_DIR)) return;
  mkdirSync(OUT_DATA_DIR, { recursive: true });
  for (const entry of readdirSync(CONFIG_DIR)) {
    copyFileSync(join(CONFIG_DIR, entry), join(OUT_DATA_DIR, entry));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  copyData();
  console.log(`[copy-data] data/in/config/frontend -> ${OUT_DATA_DIR}`);
  // The untracked repo link also lives in the static methodology page.
  const { applyLinks } = await import("./apply-links.mjs");
  applyLinks();
}
