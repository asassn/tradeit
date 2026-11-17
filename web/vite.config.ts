import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * The bundle is built **into the Python package**, at
 * `src/tradeit/api/static/`, and FastAPI mounts it from there.
 *
 * That is the whole integration. There is no second server in production and
 * no CORS, because the page and its data come from the same origin — and
 * because the built bundle is committed, `pip install` alone still yields a
 * working dashboard. A developer needs Node; an operator does not.
 *
 * `base: "./"` keeps every asset reference relative, so the mount point can
 * move without rebuilding.
 */
export default defineConfig({
  plugins: [react()],
  base: "./",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../src/tradeit/api/static",
    emptyOutDir: true,
    // No source map in the shipped bundle. The map is 877 kB against a
    // 237 kB bundle and the output is COMMITTED, so every rebuild would add
    // close to a megabyte of churn to a repository that already carries a
    // 73 GB corpus beside it. A developer debugging the front end runs
    // `npm run dev`, which has full maps and hot reload.
    sourcemap: false,
  },
  server: {
    port: 5173,
    // Dev runs Vite for hot reload and proxies data to the Python service, so
    // the dev and production origins behave the same way.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
