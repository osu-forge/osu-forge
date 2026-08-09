import { body, disc, type ShaderProgram } from "@/shaders/generated";

/**
 * Raw WebGL2. No abstraction layer, deliberately.
 *
 * X-GIS has an RHI that presents WebGL2 as WebGPU, and it is not used here:
 * it is a couple of thousand lines to hide an API this file touches in about a
 * hundred, and X-GIS's own shader gallery does not use it either. The shaders
 * come from the same DSL, so the WebGPU path stays a re-emit rather than a
 * rewrite; that was the reason to want the abstraction and it is already paid
 * for.
 *
 * # Binding by name, not by number
 *
 * The authored source says `@group(0) @binding(0)`, and none of that survives
 * the GLSL emit — GLSL ES 3.00 has no such concept. The uniform block is bound
 * by its **name**, taken from the emitted reflection rather than typed in here
 * where it could drift. Vertex locations *do* survive, so those come from the
 * reflection too instead of `getAttribLocation`.
 *
 * # Why the slider body has a depth pass
 *
 * A slider body overlaps itself: every curve doubles back near its start, and a
 * repeat slider covers its whole path twice. With alpha blending each overlap
 * blends over itself and the body comes out mottled, brightest where the shape
 * is most crowded — which is where someone is trying to read it. So the body
 * pass writes depth from the distance to the centre line, tests `LESS`, and
 * does not blend. The fragment nearest the centre wins; the rest are discarded.
 * One coat of paint however many times the ribbon crosses itself.
 */

export interface Viewport {
  width: number;
  height: number;
}

/** One disc: a circle, an approach ring, or a cursor sample. */
export interface Disc {
  x: number;
  y: number;
  radius: number;
  /** Inner radius as a fraction of the outer. 0 fills; 0.8 is a thin ring. */
  inner: number;
  colour: readonly [number, number, number, number];
}

/** One slider body: a polyline, drawn as a ribbon of the object radius. */
export interface Ribbon {
  /** Interleaved x, y in osu! pixels. */
  points: Float32Array;
  /** Where in `points` this ribbon starts, in points. */
  offset: number;
  /** How many points it spans. */
  count: number;
  colour: readonly [number, number, number, number];
}

const FIELD_WIDTH = 512;
const FIELD_HEIGHT = 384;

const DISC_FLOATS = 8; // centre 2, shape 2, tint 4
const BODY_FLOATS = 9; // point 2, normal 2, side 1, tint 4
const BODY_TINT_OFFSET = 20; // bytes into the vertex: (2 + 2 + 1) floats

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("could not create a shader object");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? "(no log)";
    gl.deleteShader(shader);
    // The line numbers refer to emitted GLSL, so the log is printed whole
    // rather than summarised — mapping it back to the authored source by hand
    // is the only way through, and a truncated log makes that impossible.
    throw new Error(`shader failed to compile:\n${log}\n---\n${source}`);
  }
  return shader;
}

function link(gl: WebGL2RenderingContext, program: ShaderProgram): WebGLProgram {
  const vertex = compile(gl, gl.VERTEX_SHADER, program.vertex);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, program.fragment);
  const linked = gl.createProgram();
  if (!linked) throw new Error("could not create a program object");
  gl.attachShader(linked, vertex);
  gl.attachShader(linked, fragment);
  gl.linkProgram(linked);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(linked, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(linked) ?? "(no log)";
    gl.deleteProgram(linked);
    throw new Error(`program failed to link: ${log}`);
  }
  return linked;
}

function bindUniformBlock(
  gl: WebGL2RenderingContext,
  linked: WebGLProgram,
  program: ShaderProgram,
  index: number,
): void {
  const block = gl.getUniformBlockIndex(linked, program.uniformBlock);
  if (block === gl.INVALID_INDEX) {
    throw new Error(
      `no uniform block named ${program.uniformBlock}. GLSL has no @group/@binding, ` +
        "so the name from the emitted reflection is the only handle there is.",
    );
  }
  gl.uniformBlockBinding(linked, block, index);
}

export class PlayfieldRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly discProgram: WebGLProgram;
  private readonly bodyProgram: WebGLProgram;
  private readonly view: WebGLBuffer;
  private readonly viewData: Float32Array;
  private readonly discVao: WebGLVertexArrayObject;
  private readonly discInstances: WebGLBuffer;
  private readonly bodyVao: WebGLVertexArrayObject;
  private readonly bodyVertices: WebGLBuffer;
  private discScratch = new Float32Array(0);
  private bodyScratch = new Float32Array(0);

  constructor(canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false, // The shaders feather their own edges; MSAA on top costs memory for nothing.
      depth: true,
      premultipliedAlpha: false,
      powerPreference: "low-power",
    });
    if (!gl) throw new Error("WebGL2 is not available in this browser");
    this.gl = gl;

    this.discProgram = link(gl, disc);
    this.bodyProgram = link(gl, body);
    bindUniformBlock(gl, this.discProgram, disc, 0);
    bindUniformBlock(gl, this.bodyProgram, body, 0);

    this.viewData = new Float32Array(disc.uniformSize / 4);
    const view = gl.createBuffer();
    if (!view) throw new Error("could not create the uniform buffer");
    this.view = view;
    gl.bindBuffer(gl.UNIFORM_BUFFER, view);
    gl.bufferData(gl.UNIFORM_BUFFER, disc.uniformSize, gl.DYNAMIC_DRAW);
    gl.bindBufferBase(gl.UNIFORM_BUFFER, 0, view);

    // Discs: one unit quad, instanced. A two-thousand-object map is one draw.
    const quad = gl.createBuffer();
    const discVao = gl.createVertexArray();
    const discInstances = gl.createBuffer();
    if (!quad || !discVao || !discInstances) throw new Error("could not create disc buffers");
    this.discVao = discVao;
    this.discInstances = discInstances;

    gl.bindVertexArray(discVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
      gl.STATIC_DRAW,
    );
    gl.enableVertexAttribArray(disc.attributes.corner!);
    gl.vertexAttribPointer(disc.attributes.corner!, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, discInstances);
    const stride = DISC_FLOATS * 4;
    const layout: [number, number, number][] = [
      [disc.attributes.centre!, 2, 0],
      [disc.attributes.shape!, 2, 8],
      [disc.attributes.tint!, 4, 16],
    ];
    for (const [location, size, offset] of layout) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset);
      gl.vertexAttribDivisor(location, 1);
    }

    const bodyVao = gl.createVertexArray();
    const bodyVertices = gl.createBuffer();
    if (!bodyVao || !bodyVertices) throw new Error("could not create body buffers");
    this.bodyVao = bodyVao;
    this.bodyVertices = bodyVertices;

    gl.bindVertexArray(bodyVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, bodyVertices);
    const bodyStride = BODY_FLOATS * 4;
    const bodyLayout: [number, number, number][] = [
      [body.attributes.point!, 2, 0],
      [body.attributes.normal!, 2, 8],
      [body.attributes.side!, 1, 16],
    ];
    for (const [location, size, offset] of bodyLayout) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, bodyStride, offset);
    }
    gl.enableVertexAttribArray(body.attributes.tint!);
    gl.vertexAttribPointer(body.attributes.tint!, 4, gl.FLOAT, false, bodyStride, BODY_TINT_OFFSET);

    gl.bindVertexArray(null);
  }

  /** Set the transform for this frame. Returns the scale, for sizing in pixels. */
  private setView(viewport: Viewport, radius: number): number {
    const gl = this.gl;
    const scale = Math.min(viewport.width / FIELD_WIDTH, viewport.height / FIELD_HEIGHT) * 0.94;
    const drawWidth = FIELD_WIDTH * scale;
    const drawHeight = FIELD_HEIGHT * scale;
    const left = (viewport.width - drawWidth) / 2;
    const top = (viewport.height - drawHeight) / 2;

    // osu! pixels to clip space. The y scale is negative because osu! y grows
    // downward and clip space y grows up; keeping the flip here means no other
    // code has to remember it.
    const sx = (2 * scale) / viewport.width;
    const sy = (-2 * scale) / viewport.height;
    const ox = (2 * left) / viewport.width - 1;
    const oy = 1 - (2 * top) / viewport.height;

    const offsets = disc.uniformOffsets;
    this.viewData[offsets.transform! / 4] = sx;
    this.viewData[offsets.transform! / 4 + 1] = sy;
    this.viewData[offsets.transform! / 4 + 2] = ox;
    this.viewData[offsets.transform! / 4 + 3] = oy;
    this.viewData[offsets.radius! / 4] = radius;
    // One device pixel of feather, expressed in the quad's -1..1 space, so the
    // edge is the same softness whatever the zoom.
    this.viewData[offsets.feather! / 4] = Math.min(0.5, 1.5 / Math.max(1, radius * scale));

    gl.bindBuffer(gl.UNIFORM_BUFFER, this.view);
    gl.bufferSubData(gl.UNIFORM_BUFFER, 0, this.viewData);
    return scale;
  }

  draw(viewport: Viewport, radius: number, ribbons: Ribbon[], discs: Disc[]): void {
    const gl = this.gl;
    gl.viewport(0, 0, viewport.width, viewport.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    this.setView(viewport, radius);

    if (ribbons.length > 0) {
      // Depth on, blending off: overlaps resolve by which fragment is nearest
      // the centre line rather than by accumulating.
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LESS);
      gl.disable(gl.BLEND);
      gl.useProgram(this.bodyProgram);
      gl.bindVertexArray(this.bodyVao);

      let vertices = 0;
      for (const ribbon of ribbons) vertices += ribbon.count * 2;
      const needed = vertices * BODY_FLOATS;
      if (this.bodyScratch.length < needed) this.bodyScratch = new Float32Array(needed * 2);
      const scratch = this.bodyScratch;

      // One strip per ribbon, so each is its own draw call. Concatenating them
      // into one buffer would join the end of each to the start of the next
      // with a triangle across the playfield.
      const starts: [number, number][] = [];
      let at = 0;
      for (const ribbon of ribbons) {
        const start = at / BODY_FLOATS;
        const [r, g, b, a] = ribbon.colour;
        for (let i = 0; i < ribbon.count; i++) {
          const index = (ribbon.offset + i) * 2;
          const x = ribbon.points[index]!;
          const y = ribbon.points[index + 1]!;
          // Normal from the neighbouring points, so a corner gets the average
          // of the two segments meeting there rather than one of them.
          const previous = Math.max(0, i - 1);
          const next = Math.min(ribbon.count - 1, i + 1);
          const dx = ribbon.points[(ribbon.offset + next) * 2]! -
            ribbon.points[(ribbon.offset + previous) * 2]!;
          const dy = ribbon.points[(ribbon.offset + next) * 2 + 1]! -
            ribbon.points[(ribbon.offset + previous) * 2 + 1]!;
          const magnitude = Math.hypot(dx, dy) || 1;
          const nx = -dy / magnitude;
          const ny = dx / magnitude;
          for (const side of [-1, 1]) {
            scratch[at++] = x;
            scratch[at++] = y;
            scratch[at++] = nx;
            scratch[at++] = ny;
            scratch[at++] = side;
            scratch[at++] = r;
            scratch[at++] = g;
            scratch[at++] = b;
            scratch[at++] = a;
          }
        }
        starts.push([start, ribbon.count * 2]);
      }

      gl.bindBuffer(gl.ARRAY_BUFFER, this.bodyVertices);
      gl.bufferData(gl.ARRAY_BUFFER, scratch.subarray(0, at), gl.DYNAMIC_DRAW);
      for (const [first, count] of starts) {
        gl.drawArrays(gl.TRIANGLE_STRIP, first, count);
      }
    }

    if (discs.length > 0) {
      gl.disable(gl.DEPTH_TEST);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(this.discProgram);
      gl.bindVertexArray(this.discVao);

      const needed = discs.length * DISC_FLOATS;
      if (this.discScratch.length < needed) this.discScratch = new Float32Array(needed * 2);
      const scratch = this.discScratch;
      let at = 0;
      for (const item of discs) {
        scratch[at++] = item.x;
        scratch[at++] = item.y;
        scratch[at++] = item.radius;
        scratch[at++] = item.inner;
        scratch[at++] = item.colour[0];
        scratch[at++] = item.colour[1];
        scratch[at++] = item.colour[2];
        scratch[at++] = item.colour[3];
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.discInstances);
      gl.bufferData(gl.ARRAY_BUFFER, scratch.subarray(0, at), gl.DYNAMIC_DRAW);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, discs.length);
    }

    gl.bindVertexArray(null);
  }

  dispose(): void {
    const gl = this.gl;
    gl.deleteProgram(this.discProgram);
    gl.deleteProgram(this.bodyProgram);
    gl.deleteBuffer(this.view);
    gl.deleteBuffer(this.discInstances);
    gl.deleteBuffer(this.bodyVertices);
    gl.deleteVertexArray(this.discVao);
    gl.deleteVertexArray(this.bodyVao);
  }
}
