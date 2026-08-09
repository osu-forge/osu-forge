# @xgis/shader-dsl

Shaders are authored as typed TypeScript expressions, and one IR emits GLSL ES
3.00 for WebGL2 and WGSL for WebGPU from the same source.

| | |
|---|---|
| Source | <https://github.com/X-GIS/X-GIS/tree/main/shader-dsl> |
| Copied from | `D:\X-GIS\shader-dsl` |
| Commit | `d2bc9260afddf078f7192e4f74bde0fd012f5f1b` |
| Copied on | 2026-08-09 |
| Licence | MIT — see `LICENSE` |
| Vendored | `src/` minus tests (82 files), `LICENSE`, `AUTHORING.md` |

The 87 `*.test.ts` files are not copied. They are written for a runner this
project does not have, are never executed here, and would double the vendored
size with files nothing reads. The emit is verified by its output instead — see
the check below.

## Build time only, never runtime

This is the rule the arrangement exists to enforce. The DSL runs in Node during
`npm run shaders`; what reaches the browser is the GLSL it printed and the
reflection describing how to bind it. The DSL is not a runtime dependency, is
not imported from anything under `src/`, and is not in the bundle.

That is checked rather than asserted: after a build, `dist/**/*.js` contains
`#version 300 es` and the emitted function bodies, and contains none of
`emitGlslModule`, `uniformStruct`, `ioStruct`, `reflect` or `emitModule`.

## Why vendored rather than depended on

The package is `private: true` and is not published to npm, so there is no
version to depend on. Committing only the emitted GLSL was the alternative and
it breaks this as a public project: changing a shader would then need a checkout
of a repository nobody else has.

The cost is about 24,700 lines here. What it buys is that the same shader source
emits WGSL, so a WebGPU path later is a re-emit rather than a rewrite.

Vendoring is safe in the way that matters: the package declares **no
dependencies** and contains **no `node:` imports**, both verified at copy time.
Nothing is pulled in behind it.

## Not the RHI

X-GIS also has an RHI abstracting WebGL2 and WebGPU. Deliberately unused: it is
roughly 2,245 lines making WebGL2 present itself as WebGPU, and X-GIS's own
shader gallery does not use it either — `site/src/lib/shader-playground.ts` is
about ninety lines of raw GL. `src/lib/renderer.ts` here is likewise raw WebGL2.

## Two things the emit changes

- **`@group` / `@binding` do not survive it.** GLSL ES 3.00 has no such concept,
  so a uniform block binds *by name* through
  `gl.getUniformBlockIndex(program, "View")` — the group and binding numbers in
  the authored source are WGSL's and mean nothing to the WebGL2 path.
- **Vertex `location` does survive.** `reflect()` reports it, so the attribute
  table in the generated file is the source's own numbering. Binding a buffer to
  a guessed location draws something plausible, which is the worst way for this
  to fail, so the emit refuses outright if reflection reports no locations.

## Running it

```bash
npm run shaders    # shaders/*.ts -> src/shaders/generated.ts
```

`npm run build` and `npm run dev` both depend on it. The generated file is
gitignored: it is derived, like `dist/`.
