import { useEffect, useRef } from "react";
import type { ReplayHeader, Samples } from "@/lib/protocol";
import { sampleAt } from "@/lib/protocol";

/**
 * The playfield, drawn from what the replay actually recorded.
 *
 * Two decisions here are about honesty rather than about looks, and both are
 * easy to undo by making the picture prettier:
 *
 * **Cursor samples are points, not a curve.** A replay records roughly sixty
 * positions a second and nothing in between. Smoothing them into a spline
 * invents motion that was never measured, and the invented parts are exactly
 * where someone looks when asking what their hand did. The segments are drawn
 * faintly so the path reads as a path; the samples sit on top so it is always
 * visible what is measurement and what is joinery.
 *
 * **The approach circle uses the beatmap's own preempt.** Its radius is how
 * much time is left, so a wrong constant here would make every judgement look
 * mistimed by the same amount and look deliberate while doing it.
 *
 * This is Canvas 2D. The WebGL2 renderer replaces it once the shader pipeline
 * is vendored; the interface it needs — decoded samples, a path buffer, a clock
 * — is the interface this already takes.
 */

export interface PlayfieldProps {
  header: ReplayHeader;
  samples: Samples;
  paths: Float32Array;
  /** Map time, milliseconds. */
  clock: number;
  /** How many samples of cursor trail to draw behind the clock. */
  trail?: number;
}

const FIELD_WIDTH = 512;
const FIELD_HEIGHT = 384;

/** Approach circle starts at this multiple of the object radius, as osu! does. */
const APPROACH_SCALE = 2.4;

const JUDGEMENT_COLOUR = ["--color-judge-miss", "--color-judge-50", "--color-judge-100", "--color-judge-300"];

export function Playfield({ header, samples, paths, clock, trail = 48 }: PlayfieldProps) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const ctx = element.getContext("2d");
    if (!ctx) return;

    const style = getComputedStyle(document.documentElement);
    const token = (name: string) => style.getPropertyValue(name).trim() || "#ffffff";

    const box = element.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    element.width = Math.max(1, Math.round(box.width * dpr));
    element.height = Math.max(1, Math.round(box.height * dpr));

    const scale =
      Math.min(element.width / FIELD_WIDTH, element.height / FIELD_HEIGHT) * 0.94;
    const ox = (element.width - FIELD_WIDTH * scale) / 2;
    const oy = (element.height - FIELD_HEIGHT * scale) / 2;
    const px = (x: number) => ox + x * scale;
    const py = (y: number) => oy + y * scale;

    ctx.clearRect(0, 0, element.width, element.height);
    ctx.strokeStyle = token("--color-hairline");
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, FIELD_WIDTH * scale, FIELD_HEIGHT * scale);

    const radius = header.beatmap.radius * scale;
    const preempt = header.beatmap.preempt;

    for (let i = 0; i < header.objects.length; i++) {
      const object = header.objects[i]!;
      if (clock < object.t - preempt || clock > object.end + 240) continue;
      const judgement = header.judgements[i];
      const colour = token(JUDGEMENT_COLOUR[judgement?.grade ?? 3]!);

      if (object.kind === "slider" && object.p && object.p[1] > 0) {
        const [offset, count] = object.p;
        ctx.strokeStyle = token("--color-canvas-soft");
        ctx.lineWidth = radius * 2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        for (let q = 0; q < count; q++) {
          const at = (offset + q) * 2;
          const x = px(paths[at]!);
          const y = py(paths[at + 1]!);
          if (q === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();

        ctx.strokeStyle = token("--color-hairline");
        ctx.lineWidth = Math.max(1, radius * 0.08);
        ctx.stroke();
      }

      const cx = px(object.x);
      const cy = py(object.y);
      ctx.strokeStyle = colour;
      ctx.lineWidth = Math.max(1, radius * 0.1);
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();

      if (clock < object.t) {
        const remaining = (object.t - clock) / preempt;
        ctx.strokeStyle = token("--color-canvas-mid");
        ctx.lineWidth = Math.max(1, radius * 0.06);
        ctx.beginPath();
        ctx.arc(cx, cy, radius * (1 + APPROACH_SCALE * remaining), 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    const at = sampleAt(samples.t, clock);
    const from = Math.max(0, at - trail);

    ctx.strokeStyle = token("--color-canvas-mid");
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = from; i <= at; i++) {
      const x = px(samples.x[i]!);
      const y = py(samples.y[i]!);
      if (i === from) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    const held = token("--color-accent-breeze");
    const free = token("--color-body-mid");
    for (let i = from; i <= at; i++) {
      const down = samples.keys[i]! !== 0;
      ctx.fillStyle = down ? held : free;
      ctx.beginPath();
      ctx.arc(px(samples.x[i]!), py(samples.y[i]!), down ? 3 * dpr : 1.5 * dpr, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [header, samples, paths, clock, trail]);

  return <canvas ref={canvas} className="h-full w-full" />;
}
