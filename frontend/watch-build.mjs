// Dev-only: rebuild the site with a full `vite build` whenever anything
// under frontend/src changes.
//
// Why not `vite build --watch`: its incremental rebuilds re-emitted a
// cached stylesheet after edits to style.css / tailwind.css / index.html,
// so CSS changes only showed up after a container restart (reproduced
// 2026-10-12 with both the Tailwind Vite and PostCSS integrations). A full
// build takes a few seconds here and is what production uses, so the dev
// loop runs exactly that.
//
// Why polling, not fs.watch: the source tree is a bind mount into the
// container and Node's recursive fs.watch dropped some host-side edits
// there (an append to style.css produced no event). Stat-polling a few
// hundred files every 500 ms is cheap and cannot miss a change.
import { spawn } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const SRC = resolve("frontend/src");
const POLL_MS = 500;
const IGNORE = /(^|\/)(\.|node_modules\/)|~$|\.swp$/;

function signature(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (IGNORE.test(p)) continue;
    let st;
    try { st = statSync(p); } catch { continue; }
    if (st.isDirectory()) signature(p, acc);
    else acc.push(`${p}:${st.mtimeMs}:${st.size}`);
  }
  return acc;
}

let running = null;
let pending = false;

function build() {
  if (running) { pending = true; return; }
  const started = Date.now();
  running = spawn("npx", ["vite", "build"], { stdio: "inherit" });
  running.on("exit", (code) => {
    running = null;
    console.log(`[watch-build] ${code === 0 ? "ok" : `FAILED (exit ${code})`} in ${Date.now() - started} ms`);
    if (pending) { pending = false; build(); }
  });
}

let last = signature(SRC).join("\n");
build();
setInterval(() => {
  const now = signature(SRC).join("\n");
  if (now !== last) { last = now; build(); }
}, POLL_MS);
console.log(`[watch-build] polling ${SRC} every ${POLL_MS} ms`);
