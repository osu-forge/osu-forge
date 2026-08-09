/** Emit the GLSL and reflection that osu-forge actually commits. */

import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import { emitGlslModule, reflect } from "../vendor/xgis-shader-dsl/src/index.ts";
import { MODULES } from "../shaders/playfield.ts";

const OUTPUT = process.env.OSU_FORGE_SHADER_OUT!;

interface Reflection {
  uniforms?: {
    name: string;
    size: number;
    align: number;
    fields?: { name: string; type: string; offset: number; size: number }[];
  }[];
  vertex?: {
    attributes?: { name: string; location: number; type: string; offset: number }[];
    stride?: number;
  };
}

const parts: string[] = [
  "/**",
  " * Playfield shaders: GLSL ES 3.00, and the reflection needed to bind it.",
  " *",
  " * GENERATED. Authored against ../vendor/xgis-shader-dsl/src/index.ts (MIT, github.com/X-GIS/X-GIS)",
  " * and emitted from it. The DSL is not vendored here and is not a build",
  " * dependency — only its output is. Editing this file by hand works until",
  " * someone re-emits, so change the shader source and re-emit instead.",
  " *",
  " * Two things the emit does that the authored source does not show:",
  " *",
  " * `@group` and `@binding` do not survive it. GLSL ES 3.00 has no such concept,",
  " * so a uniform block is bound *by name* through",
  " * `gl.getUniformBlockIndex(program, uniformBlock)` — the group and binding",
  " * numbers in the source are WGSL's and mean nothing here.",
  " *",
  " * Vertex `location` does survive, so `attributes` below is the source's own",
  " * numbering rather than an assumption. Binding a buffer to a guessed location",
  " * draws something plausible, which is the worst way for this to fail.",
  " */",
  "",
  "export interface ShaderProgram {",
  "  readonly vertex: string;",
  "  readonly fragment: string;",
  "  /** Uniform block name; GLSL binds by name, not by group/binding. */",
  "  readonly uniformBlock: string;",
  "  /** std140 block size in bytes, including trailing padding. */",
  "  readonly uniformSize: number;",
  "  /** Field name to std140 byte offset. */",
  "  readonly uniformOffsets: Readonly<Record<string, number>>;",
  "  /** Attribute name to vertex location. */",
  "  readonly attributes: Readonly<Record<string, number>>;",
  "}",
  "",
];

for (const [name, decl] of Object.entries(MODULES)) {
  const vertex = emitGlslModule(decl as never, "vertex");
  const fragment = emitGlslModule(decl as never, "fragment");
  const reflection = reflect(decl as never) as Reflection;

  const uniform = reflection.uniforms?.[0];
  if (!uniform) throw new Error(`${name}: no uniform block; the renderer would bind nothing`);

  const offsets: Record<string, number> = {};
  for (const field of uniform.fields ?? []) offsets[field.name] = field.offset;

  const attributes: Record<string, number> = {};
  for (const attribute of reflection.vertex?.attributes ?? []) {
    attributes[attribute.name] = attribute.location;
  }
  if (Object.keys(attributes).length === 0) {
    throw new Error(
      `${name}: reflection reported no attribute locations, so the renderer would ` +
        "guess them — which is how a buffer ends up on the wrong input and draws " +
        "something plausible.",
    );
  }

  parts.push(
    `export const ${name}: ShaderProgram = {`,
    `  vertex: ${JSON.stringify(vertex)},`,
    `  fragment: ${JSON.stringify(fragment)},`,
    `  uniformBlock: ${JSON.stringify(uniform.name)},`,
    `  uniformSize: ${uniform.size},`,
    `  uniformOffsets: ${JSON.stringify(offsets)},`,
    `  attributes: ${JSON.stringify(attributes)},`,
    "};",
    "",
  );

  console.log(
    `${name}: vs ${vertex.length}B fs ${fragment.length}B  ` +
      `${uniform.name} ${uniform.size}B  attrs ${Object.entries(attributes)
        .map(([key, value]) => `${key}=${value}`)
        .join(" ")}`,
  );
}

await mkdir(dirname(OUTPUT), { recursive: true });
await writeFile(OUTPUT, parts.join("\n"), "utf8");
console.log("wrote", OUTPUT);
