// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

/**
 * Static output on purpose. The Python server serves these files; there is no
 * Node process in a release and nothing here renders on demand. Every dynamic
 * part of the page is a React island fed by the local API.
 *
 * `outDir` sits inside `web/` and is not committed. The build is reproducible
 * from this checkout, and a committed `dist/` is a second copy of the source
 * that drifts from it silently.
 */
export default defineConfig({
  output: "static",
  outDir: "./dist",
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
    build: {
      // The page is loaded from 127.0.0.1 by a browser the user already has
      // open; a few extra requests cost nothing and inlining assets would make
      // the served HTML harder to inspect, which is the opposite of the point.
      assetsInlineLimit: 0,
    },
  },
});
