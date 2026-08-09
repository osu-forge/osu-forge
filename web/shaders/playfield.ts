/**
 * osu-forge playfield shaders, authored against ../vendor/xgis-shader-dsl/src/index.ts.
 *
 * Authored here and emitted at build time. The DSL runs in Node and never reaches the browser bundle; what ships is the GLSL it printed.
 *
 * Two decisions the shaders encode:
 *
 * A slider body is a thick line that overlaps itself; every curve doubles back
 * near its own start and a repeat slider covers its path twice. Alpha blending
 * makes each overlap blend over itself, so the body comes out mottled and
 * brightest exactly where the shape is most crowded. So the body writes depth
 * from the distance to its centre line, tests LESS, and does not blend: the
 * fragment nearest the centre wins and the rest are discarded. One coat of
 * paint however many times the ribbon crosses itself.
 *
 * Circles, approach circles and cursor samples are all "a disc at a position,
 * with a radius and a colour", so one instanced quad draws all of them and a
 * two-thousand-object map costs a handful of draw calls.
 */

import {
  abs,
  builtin,
  Discard,
  f32,
  f32T,
  fn,
  If,
  ioStruct,
  length,
  location,
  mix,
  module,
  smoothstep,
  uniformStruct,
  vec2fT,
  vec3,
  vec4,
  vec4fT,
} from "../vendor/xgis-shader-dsl/src/index.ts";

export const View = uniformStruct(
  "View",
  { group: 0, binding: 0, as: "view" },
  {
    /** `(scale_x, scale_y, offset_x, offset_y)`, applied as `p * scale + offset`.
     *  Carries the y flip: osu! pixels grow downward and clip space grows up,
     *  so the sign lives here rather than at three call sites that then
     *  disagree. */
    transform: vec4fT,
    /** Object radius in osu! pixels, from the map's circle size. */
    radius: f32T,
    /** Feather width, so edges stay smooth at any zoom. */
    feather: f32T,
  },
);

const DiscOut = ioStruct("DiscOut", {
  pos: builtin("position", vec4fT),
  local: location(0, vec2fT),
  tint: location(1, vec4fT),
  inner: location(2, f32T),
});

const discVertex = fn(
  "disc_vs",
  {
    corner: location(0, vec2fT),
    centre: location(1, vec2fT),
    shape: location(2, vec2fT),
    tint: location(3, vec4fT),
  },
  (p) => {
    const t = View.field.transform;
    const world = p.centre.add(p.corner.mul(p.shape.x));
    return DiscOut.construct({
      pos: vec4(world.x.mul(t.x).add(t.z), world.y.mul(t.y).add(t.w), f32(0), f32(1)),
      local: p.corner,
      tint: p.tint,
      inner: p.shape.y,
    });
  },
  { stage: "vertex" },
);

const discFragment = fn(
  "disc_fs",
  { in: DiscOut.type },
  (p) => {
    const pin = DiscOut.of(p.in);
    const d = length(pin.local);
    const feather = View.field.feather;
    // Both edges from one distance, so a ring is this shader with `inner` above
    // zero rather than a second program kept looking identical by hand.
    const outer = smoothstep(f32(1), f32(1).sub(feather), d);
    const inner = smoothstep(pin.inner.sub(feather), pin.inner, d);
    const alpha = outer.mul(inner).mul(pin.tint.w);
    If(alpha.lt(0.002), () => {
      Discard();
    });
    return vec4(pin.tint.swizzle<"vec3<f32>">("rgb"), alpha);
  },
  { stage: "fragment", retAttr: "@location(0)" },
);

const BodyOut = ioStruct("BodyOut", {
  pos: builtin("position", vec4fT),
  offset: location(0, f32T),
  tint: location(1, vec4fT),
});

const bodyVertex = fn(
  "body_vs",
  {
    point: location(0, vec2fT),
    normal: location(1, vec2fT),
    side: location(2, f32T),
    tint: location(3, vec4fT),
  },
  (p) => {
    const t = View.field.transform;
    const world = p.point.add(p.normal.mul(p.side.mul(View.field.radius)));
    const depth = abs(p.side).mul(0.5);
    return BodyOut.construct({
      pos: vec4(world.x.mul(t.x).add(t.z), world.y.mul(t.y).add(t.w), depth, f32(1)),
      offset: p.side,
      tint: p.tint,
    });
  },
  { stage: "vertex" },
);

const bodyFragment = fn(
  "body_fs",
  { in: BodyOut.type },
  (p) => {
    const pin = BodyOut.of(p.in);
    const d = abs(pin.offset);
    const feather = View.field.feather;
    // A border at the ribbon's edge, which is what makes two adjacent sliders
    // read as two rather than as one wide smear.
    const edge = smoothstep(f32(1).sub(feather.mul(6)), f32(1), d);
    const body = mix(
      pin.tint.swizzle<"vec3<f32>">("rgb"),
      vec3(f32(1), f32(1), f32(1)),
      edge.mul(0.35),
    );
    const alpha = smoothstep(f32(1), f32(1).sub(feather), d).mul(pin.tint.w);
    If(alpha.lt(0.002), () => {
      Discard();
    });
    return vec4(body, alpha);
  },
  { stage: "fragment", retAttr: "@location(0)" },
);

export const MODULES = {
  disc: module({
    structs: [View.struct, DiscOut.decl],
    bindings: [View.binding],
    funcs: [discVertex, discFragment],
  }),
  body: module({
    structs: [View.struct, BodyOut.decl],
    bindings: [View.binding],
    funcs: [bodyVertex, bodyFragment],
  }),
} as const;
