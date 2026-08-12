import { useMemo, useState } from "react";
import type { CorpusProgress, ProgressPoint, ProgressSide } from "@/lib/protocol";
import { ChartTooltip } from "@/components/ChartTooltip";
import {
  ACCENT,
  AXIS_TEXT,
  CHART_WIDTH,
  GRID,
  MUTED,
  SURFACE,
  linear,
  niceStep,
} from "@/lib/chart";
import { monthDay, signed } from "@/lib/format";
import type { Translator } from "@/i18n";

/**
 * The corpus over time: one dot per sitting, and the two eras' intervals.
 *
 * The picture answers "did it change" the way the estimator does. Each dot is
 * one session's mean — a description, no interval, because one session is one
 * cluster and one cluster earns no interval. The only intervals drawn are the
 * two bands, one per era, because that is where there are enough sessions to
 * earn one. Everything is one quantity on one axis; before and after are told
 * apart by which side of the boundary they sit on, never by a second hue.
 *
 * The x axis is session order, not calendar time. Sessions are the unit the
 * statistics cluster on, and a three-week gap stretched to scale would spend
 * most of the picture on silence.
 *
 * Colors come from the page's own tokens so the chart follows the theme; the
 * data wears the one accent, text wears text tokens, and the grid stays
 * recessive.
 */

export interface ProgressChartProps {
  progress: CorpusProgress;
  t: Translator;
}

const WIDTH = CHART_WIDTH;
const HEIGHT = 240;
const MARGIN = { top: 24, right: 16, bottom: 28, left: 48 } as const;

interface Placed {
  point: ProgressPoint;
  x: number;
  y: number;
}

export function ProgressChart({ progress, t }: ProgressChartProps) {
  const [hover, setHover] = useState<Placed | null>(null);

  const drawn = progress.points.filter((point) => point.mean_error !== null);
  const shift = progress.shift;
  const boundary = progress.boundary;

  const geometry = useMemo(() => {
    if (drawn.length === 0) return null;

    const values: number[] = [0];
    for (const point of drawn) values.push(point.mean_error ?? 0);
    for (const side of [shift?.before, shift?.after]) {
      if (side?.ci_low != null) values.push(side.ci_low);
      if (side?.ci_high != null) values.push(side.ci_high);
    }
    const low = Math.min(...values);
    const high = Math.max(...values);
    const pad = Math.max((high - low) * 0.15, 1);
    const y0 = low - pad;
    const y1 = high + pad;

    const plotW = WIDTH - MARGIN.left - MARGIN.right;
    const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
    // One session is one dot and no scale: with nothing to be spaced against
    // it sits in the middle rather than at an end, which is where a division by
    // a zero-wide domain would otherwise put it.
    const along = linear([0, drawn.length - 1], [MARGIN.left, MARGIN.left + plotW]);
    const xAt = (index: number) =>
      drawn.length === 1 ? MARGIN.left + plotW / 2 : along(index);
    // Inverted on purpose: later is up, which is the direction a reader already
    // expects a larger number to be.
    const yAt = linear([y1, y0], [MARGIN.top, MARGIN.top + plotH]);

    const placed: Placed[] = drawn.map((point, index) => ({
      point,
      x: xAt(index),
      y: yAt(point.mean_error ?? 0),
    }));

    // The boundary sits between the last session before it and the first at
    // or after it — a seam in the sequence, not a moment on a time scale.
    let boundaryX: number | null = null;
    if (boundary) {
      const firstAfter = drawn.findIndex((point) => point.started_at >= boundary.at);
      if (firstAfter > 0) boundaryX = (xAt(firstAfter - 1) + xAt(firstAfter)) / 2;
      else if (firstAfter === 0) boundaryX = MARGIN.left;
    }

    const step = niceStep(y1 - y0);
    const ticks: number[] = [];
    for (let tick = Math.ceil(y0 / step) * step; tick <= y1; tick += step) {
      ticks.push(Math.abs(tick) < 1e-9 ? 0 : tick);
    }

    const half = drawn.length > 1 ? (xAt(1) - xAt(0)) / 2 : plotW / 2;
    const band = (side: ProgressSide, indices: number[]) => {
      if (side.ci_low == null || side.ci_high == null || side.mean == null) return null;
      if (indices.length === 0) return null;
      const from = Math.max(MARGIN.left, xAt(indices[0]!) - half);
      const to = Math.min(WIDTH - MARGIN.right, xAt(indices[indices.length - 1]!) + half);
      return {
        x: from,
        width: to - from,
        top: yAt(side.ci_high),
        height: Math.max(1, yAt(side.ci_low) - yAt(side.ci_high)),
        meanY: yAt(side.mean),
      };
    };

    let bands: { before: ReturnType<typeof band>; after: ReturnType<typeof band> } | null = null;
    if (shift && boundary) {
      const beforeIndices: number[] = [];
      const afterIndices: number[] = [];
      drawn.forEach((point, index) => {
        (point.started_at < boundary.at ? beforeIndices : afterIndices).push(index);
      });
      bands = {
        before: band(shift.before, beforeIndices),
        after: band(shift.after, afterIndices),
      };
    }

    return { placed, boundaryX, ticks, yAt, bands };
  }, [drawn, shift, boundary]);

  if (!geometry) return null;
  const { placed, boundaryX, ticks, yAt, bands } = geometry;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="block h-auto w-full"
        role="img"
        aria-label={t("progress.chartLabel")}
      >
        {/* Grid first, so everything draws over it. */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={MARGIN.left}
              x2={WIDTH - MARGIN.right}
              y1={yAt(tick)}
              y2={yAt(tick)}
              stroke={GRID}
              strokeWidth={tick === 0 ? 1.5 : 0.5}
            />
            <text
              x={MARGIN.left - 8}
              y={yAt(tick) + 3.5}
              textAnchor="end"
              {...AXIS_TEXT}
            >
              {tick > 0 ? `+${tick}` : tick}
            </text>
          </g>
        ))}

        {/* The two eras' intervals — the only interval claims in the picture. */}
        {bands &&
          (["before", "after"] as const).map((era) => {
            const box = bands[era];
            if (!box) return null;
            return (
              <g key={era}>
                <rect
                  x={box.x}
                  y={box.top}
                  width={box.width}
                  height={box.height}
                  fill={ACCENT}
                  opacity={0.13}
                />
                <line
                  x1={box.x}
                  x2={box.x + box.width}
                  y1={box.meanY}
                  y2={box.meanY}
                  stroke={ACCENT}
                  strokeWidth={2}
                />
                <text
                  x={box.x + 6}
                  y={box.top - 4}
                  fontSize={10}
                  fill={MUTED}
                  style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
                >
                  {t(era === "before" ? "progress.before" : "progress.after")}
                </text>
              </g>
            );
          })}

        {/* The boundary — a seam between sessions, labelled for what it is. */}
        {boundaryX !== null && boundary && (
          <g>
            <line
              x1={boundaryX}
              x2={boundaryX}
              y1={MARGIN.top - 6}
              y2={HEIGHT - MARGIN.bottom}
              stroke={MUTED}
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <text
              x={boundaryX > WIDTH * 0.7 ? boundaryX - 5 : boundaryX + 5}
              y={MARGIN.top - 10}
              textAnchor={boundaryX > WIDTH * 0.7 ? "end" : "start"}
              fontSize={10}
              fill={MUTED}
              style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
            >
              {t(
                boundary.kind === "settings"
                  ? "progress.boundarySettings"
                  : "progress.boundaryMidpoint",
              )}
            </text>
          </g>
        )}

        {/* One dot per sitting, ringed with the surface so overlaps separate. */}
        {placed.map((item) => (
          <g key={item.point.session}>
            <circle cx={item.x} cy={item.y} r={4} fill={ACCENT} stroke={SURFACE} strokeWidth={2} />
            <circle
              cx={item.x}
              cy={item.y}
              r={12}
              fill="transparent"
              onMouseEnter={() => setHover(item)}
              onMouseLeave={() => setHover(null)}
            />
          </g>
        ))}

        {/* First and last session dates anchor the sequence in time. */}
        {placed.length > 0 && (
          <>
            <text
              x={placed[0]!.x}
              y={HEIGHT - 8}
              textAnchor="start"
              {...AXIS_TEXT}
            >
              {monthDay(placed[0]!.point.started_at)}
            </text>
            {placed.length > 1 && (
              <text
                x={placed[placed.length - 1]!.x}
                y={HEIGHT - 8}
                textAnchor="end"
                {...AXIS_TEXT}
              >
                {monthDay(placed[placed.length - 1]!.point.started_at)}
              </text>
            )}
          </>
        )}
      </svg>

      {hover && (
        <ChartTooltip x={hover.x} y={hover.y} height={HEIGHT} gap={10}>
          <div className="font-mono tabular-nums text-ink">{signed(hover.point.mean_error)} ms</div>
          <div className="mt-xxs whitespace-nowrap text-mute">
            {monthDay(hover.point.started_at)} · {t("replays.count", { n: hover.point.replays })} ·{" "}
            {hover.point.hits} hits
          </div>
        </ChartTooltip>
      )}

      <details className="mt-sm">
        <summary className="eyebrow cursor-pointer">{t("progress.table")}</summary>
        <table className="mt-sm w-full text-body-sm">
          <thead>
            <tr className="eyebrow text-left">
              <th className="py-xs font-normal">{t("progress.tableSession")}</th>
              <th className="py-xs font-normal">{t("corpus.replays")}</th>
              <th className="py-xs font-normal">{t("corpus.hits")}</th>
              <th className="py-xs font-normal">{t("analysis.meanError")}</th>
              <th className="py-xs font-normal">{t("progress.tableSpread")}</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums text-body">
            {drawn.map((point) => (
              <tr key={point.session} className="hairline-b last:border-0">
                <td className="py-xs">{monthDay(point.started_at)}</td>
                <td className="py-xs">{point.replays}</td>
                <td className="py-xs">{point.hits}</td>
                <td className="py-xs">{signed(point.mean_error, 2)} ms</td>
                <td className="py-xs">
                  {point.spread_ms === null ? "—" : `${point.spread_ms.toFixed(1)} ms`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
