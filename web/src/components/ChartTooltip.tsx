import type { ReactNode } from "react";
import { CHART_WIDTH } from "@/lib/chart";

/**
 * The box that says what the mark under the pointer is.
 *
 * It sits outside the `<svg>`, in the ordinary document, because SVG has no
 * text wrapping and no box model to lay a two-line readout out with. That means
 * it is positioned in percentages of the chart's own box: the chart is drawn in
 * view units and scaled to whatever column it lands in, so a tooltip placed at
 * a pixel offset would drift away from its mark on every screen but the one it
 * was written on.
 *
 * `pointer-events-none` for the reason that is only obvious once it has
 * happened: a tooltip that accepts the pointer takes it away from the mark that
 * summoned it, the mark's `mouseleave` fires, the tooltip closes, the pointer
 * lands back on the mark, and the whole thing flickers at whatever rate the
 * browser can manage.
 */

export interface ChartTooltipProps {
  /** Where the tooltip points, in the chart's own view units. */
  x: number;
  y: number;
  /** The height of that view. The width is {@link CHART_WIDTH}, which every
   *  chart shares; the height is each chart's own. */
  height: number;
  /**
   * Which part of the box sits over `x`.
   *
   * `center` unless the mark is near an edge, where a centred box hangs off the
   * panel and is clipped — a narrow aside is where that shows up first.
   */
  align?: "start" | "center" | "end";
  /** How far above the mark the box floats, in view units. */
  gap?: number;
  children: ReactNode;
}

const SHIFT = { start: "0", center: "-50%", end: "-100%" } as const;

export function ChartTooltip({
  x,
  y,
  height,
  align = "center",
  gap = 8,
  children,
}: ChartTooltipProps) {
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-sm border border-hairline bg-canvas-card px-md py-xs text-body-sm"
      style={{
        left: `${(x / CHART_WIDTH) * 100}%`,
        top: `${(y / height) * 100}%`,
        transform: `translate(${SHIFT[align]}, calc(-100% - ${gap}px))`,
      }}
    >
      {children}
    </div>
  );
}
