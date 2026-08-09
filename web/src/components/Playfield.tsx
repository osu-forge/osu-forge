import { useEffect, useMemo, useRef } from "react";
import type { ReplayHeader, Samples } from "@/lib/protocol";
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

/** Approach ring starts at this multiple of the object radius, as osu! does. */
const APPROACH_SCALE = 2.4;

/** How long after its end an object stays on screen, in milliseconds. */
const LINGER = 240;

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

export function Playfield({ header, samples, paths, clock, trail = 48 }: PlayfieldProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<PlayfieldRenderer | null>(null);
  const failure = useRef<string | null>(null);

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

      if (object.kind === "slider" && object.p && object.p[1] > 0) {
        ribbons.push({
          points: paths,
          offset: object.p[0],
          count: object.p[1],
          colour: palette.body,
        });
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
    }

    const at = sampleAt(samples.t, clock);
    const from = Math.max(0, at - trail);
    for (let i = from; i <= at; i++) {
      const down = samples.keys[i]! !== 0;
      discs.push({
        x: samples.x[i]!,
        y: samples.y[i]!,
        // Sized in osu! pixels so a sample is the same size relative to the
        // objects at any window size.
        radius: down ? 4.5 : 2.4,
        inner: 0,
        colour: down ? palette.held : palette.free,
      });
    }

    active.draw({ width: element.width, height: element.height }, radius, ribbons, discs);
  }, [header, samples, paths, clock, trail, palette]);

  if (failure.current) {
    return (
      <div className="flex h-full items-center justify-center p-xl">
        <p className="max-w-[60ch] text-body-sm text-body">{failure.current}</p>
      </div>
    );
  }

  return <canvas ref={canvas} className="h-full w-full" />;
}
