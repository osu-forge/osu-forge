import { useEffect, useRef } from "react";
import type { ReplayHeader } from "@/lib/protocol";
import type { Key } from "@/i18n";

/**
 * Every judgement against the windows it was judged by.
 *
 * The point of this chart is the bands. "You were 40 ms late" means nothing on
 * its own — it means one thing on an OD 4 map where the 300 window runs to 56
 * and something else entirely on OD 9 where it ends at 26. So the windows are
 * drawn to scale from the map's own values, and a hit is a mark at its own
 * error inside them. Reading how late something was becomes looking at which
 * band it fell in, which is what a player actually wants to know.
 *
 * The vertical scale is fixed to the 50 window plus a margin, so two maps are
 * not silently drawn at different scales.
 */

export interface ErrorTimelineProps {
  header: ReplayHeader;
  /** Map time, milliseconds. Drawn as a playhead. */
  clock: number;
  onSeek?: (time: number) => void;
  /** A–B section repeat, map milliseconds, drawn over the chart. */
  loop?: [number, number] | null;
  t: (key: Key, values?: Record<string, string | number>) => string;
}

const HEIGHT = 96;
/** How far past the 50 window the axis extends, as a multiple of it. */
const HEADROOM = 1.15;

export function ErrorTimeline({ header, clock, onSeek, loop, t }: ErrorTimelineProps) {
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
    element.height = Math.round(HEIGHT * dpr);

    const objects = header.objects;
    if (objects.length === 0) return;
    const first = objects[0]!.t;
    const last = objects[objects.length - 1]!.end;
    const span = Math.max(1, last - first);

    const [w300, w100, w50] = header.windows;
    const axis = w50 * HEADROOM;
    const mid = element.height / 2;
    const scaleY = (error: number) => mid + (error / axis) * mid;

    ctx.clearRect(0, 0, element.width, element.height);

    // Bands from the outside in, so the tighter window sits on top of the
    // looser one and each border reads as the edge of its own window.
    const bands: [number, string][] = [
      [w50, "--color-judge-50"],
      [w100, "--color-judge-100"],
      [w300, "--color-judge-300"],
    ];
    for (const [half, name] of bands) {
      const top = scaleY(-half);
      const height = scaleY(half) - top;
      ctx.fillStyle = token(name);
      ctx.globalAlpha = 0.1;
      ctx.fillRect(0, top, element.width, height);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = token("--color-hairline");
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, Math.round(top) + 0.5);
      ctx.lineTo(element.width, Math.round(top) + 0.5);
      ctx.moveTo(0, Math.round(top + height) + 0.5);
      ctx.lineTo(element.width, Math.round(top + height) + 0.5);
      ctx.stroke();
    }

    // Perfect timing, drawn because a cloud sitting consistently to one side of
    // it is the whole visual signature of an offset problem.
    ctx.strokeStyle = token("--color-body-mid");
    ctx.setLineDash([2 * dpr, 4 * dpr]);
    ctx.beginPath();
    ctx.moveTo(0, mid);
    ctx.lineTo(element.width, mid);
    ctx.stroke();
    ctx.setLineDash([]);

    const colours = [
      token("--color-judge-miss"),
      token("--color-judge-50"),
      token("--color-judge-100"),
      token("--color-judge-300"),
    ];
    const size = Math.max(1.5, 1.5 * dpr);
    for (let i = 0; i < header.judgements.length; i++) {
      const judgement = header.judgements[i]!;
      const object = objects[i];
      if (!object) continue;
      const x = ((object.t - first) / span) * element.width;
      ctx.fillStyle = colours[judgement.grade] ?? colours[3]!;
      if (judgement.error === null) {
        // A miss has no error to place, so it is drawn on the axis edges
        // rather than at zero — putting it at zero would read as a perfect hit.
        ctx.fillRect(x - size, 0, size * 2, 3 * dpr);
        ctx.fillRect(x - size, element.height - 3 * dpr, size * 2, 3 * dpr);
        continue;
      }
      ctx.beginPath();
      ctx.arc(x, scaleY(judgement.error), size, 0, Math.PI * 2);
      ctx.fill();
    }

    // The section being studied, tinted so where the loop will snap back to
    // is visible on the same chart being scrubbed.
    if (loop) {
      const from = ((loop[0] - first) / span) * element.width;
      const to = ((loop[1] - first) / span) * element.width;
      ctx.fillStyle = token("--color-accent-breeze");
      ctx.globalAlpha = 0.1;
      ctx.fillRect(from, 0, to - from, element.height);
      ctx.globalAlpha = 0.8;
      ctx.fillRect(Math.round(from), 0, 1.5, element.height);
      ctx.fillRect(Math.round(to), 0, 1.5, element.height);
      ctx.globalAlpha = 1;
    }

    const playhead = ((clock - first) / span) * element.width;
    ctx.strokeStyle = token("--color-ink");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(playhead) + 0.5, 0);
    ctx.lineTo(Math.round(playhead) + 0.5, element.height);
    ctx.stroke();
  }, [header, clock, loop]);

  function seek(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!onSeek) return;
    const element = event.currentTarget;
    const box = element.getBoundingClientRect();
    const objects = header.objects;
    if (objects.length === 0) return;
    const first = objects[0]!.t;
    const last = objects[objects.length - 1]!.end;
    const along = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    onSeek(first + along * (last - first));
  }

  return (
    <figure className="m-0">
      {/* A scrubber, not just a target: press seeks, and dragging keeps
          seeking while the button is down, so a break can be rocked back and
          forth over instead of clicked at repeatedly. */}
      <canvas
        ref={canvas}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          seek(event);
        }}
        onPointerMove={(event) => {
          if (event.buttons & 1) seek(event);
        }}
        className="block w-full cursor-crosshair touch-none"
        style={{ height: HEIGHT }}
      />
      <figcaption className="eyebrow mt-sm flex gap-lg px-lg">
        <span>{t("windows.legend")}</span>
        <span className="text-[color:var(--color-judge-300)]">
          {t("windows.300")} ±{header.windows[0]}ms
        </span>
        <span className="text-[color:var(--color-judge-100)]">
          {t("windows.100")} ±{header.windows[1]}ms
        </span>
        <span className="text-[color:var(--color-judge-50)]">
          {t("windows.50")} ±{header.windows[2]}ms
        </span>
      </figcaption>
    </figure>
  );
}
