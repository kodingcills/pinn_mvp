/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { copyFileSync, createReadStream, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { mockApi } from "./fixtures/mock.ts";

const outDir = resolve(import.meta.dirname, "../static");
const wasm = resolve(import.meta.dirname, "node_modules/@rerun-io/web-viewer/re_viewer_bg.wasm");
// Served same-origin at this path; the 70 MB wasm is not committed (see README.md).
const WASM_DIR = "rerun-web-viewer";

// Dev only: answer /api/* from fixtures, and the Rerun wasm from node_modules.
const devFixtures: Plugin = {
  name: "studio-dev-fixtures",
  apply: "serve",
  configureServer(server) {
    server.middlewares.use(async (req, res, next) => {
      const url = req.url ?? "";
      if (url === `/${WASM_DIR}/re_viewer_bg.wasm`) {
        res.setHeader("Content-Type", "application/wasm");
        createReadStream(wasm).pipe(res);
        return;
      }
      const rrdDir = process.env.STUDIO_RRD_DIR; // optional: real .rrd files named <run id>.rrd
      const rrd = url.match(/^\/api\/runs\/([^/]+)\/rrd$/);
      if (rrd && rrdDir && existsSync(join(rrdDir, `${decodeURIComponent(rrd[1])}.rrd`))) {
        createReadStream(join(rrdDir, `${decodeURIComponent(rrd[1])}.rrd`)).pipe(res);
        return;
      }
      if (!url.startsWith("/api/")) return next();
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const out = mockApi(req.method ?? "GET", url, raw ? JSON.parse(raw) : undefined);
      res.statusCode = out.status;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(out.body));
    });
  },
};

// Build: drop the wasm Vite copies from the package; put a local, gitignored copy where the app loads it.
const wasmOutsideBundle: Plugin = {
  name: "studio-wasm-outside-bundle",
  apply: "build",
  closeBundle() {
    const assets = join(outDir, "assets");
    for (const f of readdirSync(assets)) if (f.endsWith(".wasm")) rmSync(join(assets, f));
    mkdirSync(join(outDir, WASM_DIR), { recursive: true });
    copyFileSync(wasm, join(outDir, WASM_DIR, "re_viewer_bg.wasm"));
  },
};

export default defineConfig({
  base: "/",
  plugins: [react(), devFixtures, wasmOutsideBundle],
  optimizeDeps: { exclude: ["@rerun-io/web-viewer"] },
  build: { outDir, emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 600 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
