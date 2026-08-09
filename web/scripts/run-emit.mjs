/**
 * Bundle the shader emit, then run it. Build time only.
 *
 * The vendored DSL resolves directory imports — `./core/ir` meaning
 * `./core/ir/index.ts` — which is what a bundler does and what bare Node ESM
 * refuses. Rather than rewrite 169 vendored files to suit one consumer, the
 * entry point is bundled first and the bundle is what runs.
 *
 * esbuild comes with Vite, which Astro already depends on, so this adds no
 * package. The bundle goes to a temporary directory and is deleted afterwards:
 * it is a build artefact of a build step, and leaving it in the tree invites
 * someone to import it and quietly pull the DSL into the browser bundle — the
 * one thing this whole arrangement exists to prevent.
 *
 * Plain JavaScript, because this is what runs before anything can compile
 * TypeScript for us.
 */

import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { build } from "esbuild";

const here = resolve(import.meta.dirname);
const workspace = await mkdtemp(join(tmpdir(), "osu-forge-shaders-"));
const bundle = join(workspace, "emit.mjs");

process.env.OSU_FORGE_SHADER_OUT = resolve(here, "..", "src", "shaders", "generated.ts");

try {
  await build({
    entryPoints: [join(here, "emit-shaders.ts")],
    bundle: true,
    outfile: bundle,
    platform: "node",
    format: "esm",
    target: "node22",
    resolveExtensions: [".ts", ".mts", ".mjs", ".js", ".json"],
    logLevel: "warning",
  });
  await import(pathToFileURL(bundle).href);
} finally {
  await rm(workspace, { recursive: true, force: true });
}
