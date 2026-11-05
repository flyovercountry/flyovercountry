import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/postcss";
import { readdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { copyData } from "./frontend/copy-data.mjs";

const deferModule = {
  name: "defer-module-to-body",
  transformIndexHtml: {
    order: "post",
    handler(html) {
      const re = /\s*<script type="module"[^>]*><\/script>\n?/;
      const match = html.match(re);
      if (!match) return html;
      return html.replace(re, "\n").replace("</body>", `${match[0].trim()}\n</body>`);
    },
  },
};

const cleanStaleAssets = {
  name: "clean-stale-assets",
  writeBundle(options, bundle) {
    const currentFiles = new Set(Object.keys(bundle));
    const assetsDir = join(options.dir, "assets");
    try {
      for (const file of readdirSync(assetsDir)) {
        if (!currentFiles.has(`assets/${file}`)) {
          unlinkSync(join(assetsDir, file));
        }
      }
    } catch { /* assets dir may not exist on first build */ }
  },
};

const copyDataPlugin = {
  name: "copy-data",
  writeBundle() {
    copyData();
  },
};

export default defineConfig({
  plugins: [svelte(), deferModule, cleanStaleAssets, copyDataPlugin],
  // Tailwind through PostCSS inline, so no postcss.config.js under `root`.
  css: { postcss: { plugins: [tailwindcss()] } },
  root: "frontend/src",
  publicDir: "static",
  // The container points this at its writable /tmp.
  cacheDir: process.env.VITE_CACHE_DIR || "node_modules/.vite",
  build: {
    // `./run etl export` writes data/out/data/; a build must never wipe it
    // (cleanStaleAssets above is scoped to assets/ for the same reason).
    outDir: "../../data/out",
    emptyOutDir: false,
  },
});
