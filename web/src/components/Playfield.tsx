import { useEffect, useMemo, useRef } from "react";
import type { HitObject, ReplayHeader, Samples } from "@/lib/protocol";
import { sampleAt } from "@/lib/protocol";
import { PlayfieldRenderer, type Disc, type Ribbon } from "@/lib/renderer";

/**
 * The playfield, drawn from what the replay actually recorded.
 *
 * Two decisions here are about honesty rather than looks, and both are easy to
 * undo by making the picture prettier:
 *
 * **Cursor samples are points, not a curve.** A replay records roughly sixty
 * positions a second and nothing in between. Smoothing them into a spline
 * invents motion that was never measured, and the invented parts are exactly
 * where someone looks when asking what their hand did. The faint segments join
 * the samples; the samples are drawn on top so it stays visible which is which.
 *
 * **The approach ring uses the beatmap's own preempt.** Its radius is how much
 * time is left, so a wrong constant would make every judgement look mistimed by
 * the same amount — and look deliberate while doing it.
 */

export interface PlayfieldProps {
  header: ReplayHeader;
  samples: Samples;
  paths: Float32Array;
  /** Map time, milliseconds. */
  clock: number;
  /** How many cursor samples of trail to draw behind the clock. */
  trail?: number;
}

const FIELD_WIDTH = 512;
const FIELD_HEIGHT = 384;

/** Approach ring starts at this multiple of the object radius, as osu! does. */
const APPROACH_SCALE = 2.4;

/** The follow area is this multiple of the object radius while tracking.
 *  `DrawableSliderBall.FOLLOW_AREA` in ppy/osu — read from the source rather
 *  than fitted, after a value fitted to this corpus turned out to be wrong. */
const FOLLOW_AREA = 2.4;

/** How long after its end an object stays on screen, in milliseconds. */
const LINGER = 240;

/** How long a judgement mark stays up after the hit, in milliseconds. */
const JUDGEMENT_LINGER = 380;

/** A spinner's outer ring at its start, in osu! pixels. Shrinks to the centre
 *  as it runs, which is the only thing that shows how much is left. */
const SPINNER_RADIUS = 190;

/** Where the ball is along a slider at `clock`, and which way it is going.
 *
 * A repeat slider traverses its path once per slide, reversing each time. The
 * span is which traversal is in progress; an odd one runs backwards. Getting
 * this wrong puts the ball on the far end of every repeat slider, which looks
 * deliberate and is why it is worth stating.
 */
function ballAt(
  object: HitObject,
  clock: number,
): { progress: number; reversed: boolean; span: number } | null {
  const slides = object.slides ?? 1;
  const duration = object.end - object.t;
  if (duration <= 0 || slides < 1) return null;
  const spanLength = duration / slides;
  const elapsed = Math.min(Math.max(clock - object.t, 0), duration);
  const span = Math.min(slides - 1, Math.floor(elapsed / spanLength));
  const within = (elapsed - span * spanLength) / spanLength;
  const reversed = span % 2 === 1;
  return { progress: reversed ? 1 - within : within, reversed, span };
}

/** Point on a slider's exported path at `progress`, 0 to 1. */
function pathPoint(
  paths: Float32Array,
  offset: number,
  count: number,
  progress: number,
): [number, number] {
  const at = Math.min(count - 1, Math.max(0, Math.round(progress * (count - 1))));
  return [paths[(offset + at) * 2]!, paths[(offset + at) * 2 + 1]!];
}

type Colour = readonly [number, number, number, number];

function readColour(style: CSSStyleDeclaration, name: string, alpha: number): Colour {
  const raw = style.getPropertyValue(name).trim();
  const hex = raw.startsWith("#") ? raw.slice(1) : "ffffff";
  const full = hex.length === 3 ? [...hex].map((c) => c + c).join("") : hex;
  const value = Number.parseInt(full.slice(0, 6), 16);
  return [
    ((value >> 16) & 0xff) / 255,
    ((value >> 8) & 0xff) / 255,
    (value & 0xff) / 255,
    alpha,
  ];
}

function tone(colour: Colour, alpha: number): string {
  const [r, g, b, a] = colour;
  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a * alpha})`;
}

export function Playfield({ header, samples, paths, clock, trail = 48 }: PlayfieldProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<PlayfieldRenderer | null>(null);
  const failure = useRef<string | null>(null);

  // Combo numbers, derived once per replay. The count resets on a new-combo
  // flag and spinners take no number, exactly as the game does it.
  const combo = useMemo(() => {
    const numbers = new Array<number>(header.objects.length).fill(0);
    let current = 0;
    for (let i = 0; i < header.objects.length; i++) {
      const object = header.objects[i]!;
      if (object.combo) current = 0;
      if (object.kind === "spinner") continue;
      current += 1;
      numbers[i] = current;
    }
    return numbers;
  }, [header]);

  // Colours come from the design tokens rather than being duplicated here, and
  // are read once per replay rather than per frame — getComputedStyle is a
  // layout read and doing it sixty times a second is how a renderer ends up
  // slower than the canvas it replaced.
  const palette = useMemo(() => {
    if (typeof window === "undefined") return null;
    const style = getComputedStyle(document.documentElement);
    return {
      judged: [
        readColour(style, "--color-judge-miss", 1),
        readColour(style, "--color-judge-50", 1),
        readColour(style, "--color-judge-100", 1),
        readColour(style, "--color-judge-300", 1),
      ] as const,
      body: readColour(style, "--color-canvas-soft", 0.92),
      approach: readColour(style, "--color-canvas-mid", 0.9),
      held: readColour(style, "--color-accent-breeze", 1),
      free: readColour(style, "--color-body-mid", 0.75),
      press: readColour(style, "--color-accent-sunset", 1),
      cursor: readColour(style, "--color-ink", 1),
      follow: readColour(style, "--color-canvas-mid", 0.55),
    };
  }, []);

  useEffect(() => {
    const element = canvas.current;
    if (!element || renderer.current) return;
    try {
      renderer.current = new PlayfieldRenderer(element);
    } catch (error) {
      failure.current = String(error);
    }
    return () => {
      renderer.current?.dispose();
      renderer.current = null;
    };
  }, []);

  useEffect(() => {
    const element = canvas.current;
    const active = renderer.current;
    if (!element || !active || !palette) return;

    const box = element.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    element.width = Math.max(1, Math.round(box.width * dpr));
    element.height = Math.max(1, Math.round(box.height * dpr));

    const radius = header.beatmap.radius;
    const preempt = header.beatmap.preempt;
    const ribbons: Ribbon[] = [];
    const discs: Disc[] = [];

    for (let i = 0; i < header.objects.length; i++) {
      const object = header.objects[i]!;
      if (clock < object.t - preempt || clock > object.end + LINGER) continue;
      const grade = header.judgements[i]?.grade ?? 3;
      const colour = palette.judged[Math.min(3, Math.max(0, grade))]!;
      const active = clock >= object.t && clock <= object.end;

      if (object.kind === "spinner") {
        // Was missing entirely. The outer ring shrinking toward the centre is
        // the only thing that shows how much of it is left, so a spinner drawn
        // as a static circle tells a viewer nothing.
        const span = Math.max(1, object.end - object.t);
        const progress = Math.min(1, Math.max(0, (clock - object.t) / span));
        discs.push({
          x: 256,
          y: 192,
          radius: SPINNER_RADIUS * (1 - progress * 0.82),
          inner: 0.97,
          colour: active ? colour : palette.approach,
        });
        discs.push({ x: 256, y: 192, radius: radius * 0.35, inner: 0, colour });
        continue;
      }

      if (object.kind === "slider" && object.p && object.p[1] > 0) {
        const [offset, count] = object.p;
        ribbons.push({ points: paths, offset, count, colour: palette.body });

        const slides = object.slides ?? 1;
        const ball = ballAt(object, clock);

        // The repeat arrow, at whichever end the ball turns around next. A
        // repeat slider with no arrow gives no warning that it is about to
        // reverse, which is exactly when one gets dropped.
        if (slides > 1 && clock < object.end) {
          const turningAtFar = ball ? !ball.reversed : true;
          const [ax, ay] = pathPoint(paths, offset, count, turningAtFar ? 1 : 0);
          discs.push({
            x: ax,
            y: ay,
            radius: radius * 0.42,
            inner: 0.55,
            colour: palette.approach,
          });
        }

        if (ball && active) {
          const [bx, by] = pathPoint(paths, offset, count, ball.progress);
          // The follow area, which is the thing that decides whether tracking
          // holds. Drawn at the radius the game actually uses.
          discs.push({
            x: bx,
            y: by,
            radius: radius * FOLLOW_AREA,
            inner: 0.97,
            colour: palette.approach,
          });
          discs.push({ x: bx, y: by, radius: radius * 0.62, inner: 0, colour });
        }
      }

      // A ring rather than a disc: the object outline, at the object radius.
      discs.push({ x: object.x, y: object.y, radius, inner: 0.86, colour });

      if (clock < object.t) {
        const remaining = (object.t - clock) / preempt;
        const scale = 1 + APPROACH_SCALE * remaining;
        discs.push({
          x: object.x,
          y: object.y,
          radius: radius * scale,
          // Thinner as it grows, so the ring stays one line wide on screen
          // instead of becoming a thick band at the moment it appears.
          inner: 1 - 0.06 / scale,
          colour: palette.approach,
        });
      }

      // What the object was judged, marked where and when it happened. This is
      // the part osu! does not show on a replay: the grade at the moment it
      // was decided, so scrubbing to a break shows the verdict rather than
      // requiring it to be looked up.
      const since = clock - object.t;
      if (object.kind === "circle" && since >= 0 && since < JUDGEMENT_LINGER) {
        const fade = 1 - since / JUDGEMENT_LINGER;
        discs.push({
          x: object.x,
          y: object.y,
          radius: radius * (1 + 0.9 * (1 - fade)),
          inner: 0.9,
          colour: [colour[0], colour[1], colour[2], fade * 0.9],
        });
      }
    }

    // Follow points: the dotted run between consecutive objects of one combo.
    // Without them a jump reads as two unrelated circles, and which order they
    // come in is exactly what a viewer is trying to see.
    for (let i = 1; i < header.objects.length; i++) {
      const to = header.objects[i]!;
      const from = header.objects[i - 1]!;
      if (to.combo || to.kind === "spinner" || from.kind === "spinner") continue;
      if (clock < to.t - preempt || clock > to.t) continue;
      const [sx, sy] = from.kind === "slider" && from.p
        ? pathPoint(paths, from.p[0], from.p[1], (from.slides ?? 1) % 2 === 1 ? 1 : 0)
        : [from.x, from.y];
      const dx = to.x - sx;
      const dy = to.y - sy;
      const span = Math.hypot(dx, dy);
      const steps = Math.floor(span / 28);
      for (let step = 1; step < steps; step++) {
        const along = step / steps;
        discs.push({
          x: sx + dx * along,
          y: sy + dy * along,
          radius: 3,
          inner: 0,
          colour: palette.follow,
        });
      }
    }

    const at = sampleAt(samples.t, clock);
    const from = Math.max(0, at - trail);
    for (let i = from; i <= at; i++) {
      const down = samples.keys[i]! !== 0;
      const pressed = down && i > 0 && samples.keys[i - 1]! === 0;
      discs.push({
        x: samples.x[i]!,
        y: samples.y[i]!,
        // Sized in osu! pixels so a sample is the same size relative to the
        // objects at any window size.
        radius: pressed ? 7 : down ? 4.5 : 2.4,
        // The press itself is a ring rather than a dot, so the frame a key
        // went down is distinguishable from the frames it stayed down. That
        // moment is what a viewer is looking for and it was invisible before.
        inner: pressed ? 0.55 : 0,
        colour: pressed ? palette.press : down ? palette.held : palette.free,
      });
    }

    // The cursor now, between the two samples that bracket the clock. Linear
    // between two measurements is a reading of them, not an invention — the
    // game interpolates its own replay frames the same way. What is refused is
    // smoothing into a curve, which would add curvature nobody recorded. The
    // samples stay drawn underneath so the two remain distinguishable.
    const next = Math.min(samples.t.length - 1, at + 1);
    const gap = samples.t[next]! - samples.t[at]!;
    const along = gap > 0 ? Math.min(1, Math.max(0, (clock - samples.t[at]!) / gap)) : 0;
    const cx = samples.x[at]! + (samples.x[next]! - samples.x[at]!) * along;
    const cy = samples.y[at]! + (samples.y[next]! - samples.y[at]!) * along;
    discs.push({
      x: cx,
      y: cy,
      radius: 9,
      inner: 0.72,
      colour: samples.keys[at]! !== 0 ? palette.press : palette.cursor,
    });

    active.draw({ width: element.width, height: element.height }, radius, ribbons, discs);

    // Text on a 2D canvas above the GL one. Combo numbers and a millisecond
    // readout are the two things a viewer reads rather than looks at, and
    // rasterising glyphs through the geometry pipeline would be a font atlas
    // for no gain — this is a few draws a frame, not a hot path.
    const text = overlay.current;
    if (!text) return;
    text.width = element.width;
    text.height = element.height;
    const ctx = text.getContext("2d");
    if (!ctx) return;

    const scale =
      Math.min(element.width / FIELD_WIDTH, element.height / FIELD_HEIGHT) * 0.94;
    const ox = (element.width - FIELD_WIDTH * scale) / 2;
    const oy = (element.height - FIELD_HEIGHT * scale) / 2;
    const sx = (x: number) => ox + x * scale;
    const sy = (y: number) => oy + y * scale;

    ctx.clearRect(0, 0, text.width, text.height);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (let i = 0; i < header.objects.length; i++) {
      const object = header.objects[i]!;
      if (clock < object.t - preempt || clock > object.end + LINGER) continue;
      if (object.kind === "spinner") continue;

      // The combo number. Not on the wire — it is the count since the last
      // new-combo flag, which the page can derive and the server would only be
      // duplicating.
      const number = combo[i];
      if (number) {
        ctx.fillStyle = tone(palette!.cursor, 0.92);
        ctx.font = `${Math.round(radius * scale * 0.95)}px Inter, system-ui, sans-serif`;
        ctx.fillText(String(number), sx(object.x), sy(object.y));
      }

      // How late the hit was, at the moment it was judged. This is the number
      // osu! never shows on a replay and the reason to be watching here.
      const judgement = header.judgements[i];
      const since = clock - object.t;
      if (
        object.kind === "circle" &&
        judgement &&
        judgement.error !== null &&
        since >= 0 &&
        since < JUDGEMENT_LINGER
      ) {
        const fade = 1 - since / JUDGEMENT_LINGER;
        ctx.fillStyle = tone(palette!.judged[Math.min(3, judgement.grade)]!, fade);
        ctx.font = `${Math.round(radius * scale * 0.5)}px "Geist Mono", ui-monospace, monospace`;
        ctx.fillText(
          `${judgement.error > 0 ? "+" : ""}${judgement.error}`,
          sx(object.x),
          sy(object.y) - radius * scale * 1.35,
        );
      }
    }
  }, [header, samples, paths, clock, trail, palette, combo]);

  if (failure.current) {
    return (
      <div className="flex h-full items-center justify-center p-xl">
        <p className="max-w-[60ch] text-body-sm text-body">{failure.current}</p>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
      <canvas
        ref={overlay}
        className="pointer-events-none absolute inset-0 h-full w-full"
      />
    </div>
  );
}
