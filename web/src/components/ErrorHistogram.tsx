import { useMemo, useState } from "react";
import type { Analysis, ReplayHeader } from "@/lib/protocol";
import { ChartTooltip } from "@/components/ChartTooltip";
import { ACCENT, AXIS_TEXT, CHART_WIDTH, GRADE_COLOURS, GRID, linear } from "@/lib/chart";
import { signed } from "@/lib/format";
import type { Translator } from "@/i18n";

/**
 * Where this play's hits landed, against the windows that judged them.
 *
 * The distribution is the picture the numbers summarise: a shape centred left
 * of the zero line is a player hitting early whatever its width, and a wide
 * shape centred on zero is the spread no offset fixes. The judgement windows
 * are drawn behind the bars in the judgement colours the rest of the page
 * already uses, so "how close to a 100 am I" is read off the picture rather
 * than computed in the head.
 *
 * Errors arrive in real milliseconds; the windows are map milliseconds and
 * are divided by the replay's rate here, so a Double Time play shows its
 * genuinely narrower real-time windows rather than a reassuring lie.
 */

export interface ErrorHistogramProps {
  analysis: Analysis;
  header: ReplayHeader;
  t: Translator;
}

const WIDTH = CHART_WIDTH;
const HEIGHT = 132;
const MARGIN = { top: 8, right: 10, bottom: 20, left: 10 } as const;

interface Bin {
  from: number;
  to: number;
  count: number;
  x: number;
  width: number;
  y: number;
  height: number;
}

export function ErrorHistogram({ analysis, header, t }: ErrorHistogramProps) {
  const [hover, setHover] = useState<Bin | null>(null);

  const geometry = useMemo(() => {
    const errors = analysis.errors;
    if (errors.length < 5) return null;

    const rate = header.rate || 1;
    // Real-time half-widths, widest first, so each band paints over the wider
    // one behind it and the nesting reads as rings.
    const windows: { half: number; color: string }[] = [
      { half: header.windows[2] / rate, color: GRADE_COLOURS[1] },
      { half: header.windows[1] / rate, color: GRADE_COLOURS[2] },
      { half: header.windows[0] / rate, color: GRADE_COLOURS[3] },
    ];
    const domain = Math.ceil((windows[0]!.half + 4) / 5) * 5;

    const plotW = WIDTH - MARGIN.left - MARGIN.right;
    const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
    const xAt = linear([-domain, domain], [MARGIN.left, MARGIN.left + plotW]);

    // Around forty bins over the domain, at a width that stays a round number
    // of milliseconds so the tooltip ranges read cleanly.
    const binWidth = Math.max(1, Math.round((2 * domain) / 40));
    const edges: number[] = [];
    for (let edge = -domain; edge <= domain; edge += binWidth) edges.push(edge);

    const counts = new Array(edges.length - 1).fill(0) as number[];
    for (const error of errors) {
      if (error < -domain || error >= domain) continue;
      counts[Math.floor((error + domain) / binWidth)]! += 1;
    }
    const peak = Math.max(...counts, 1);

    const bins: Bin[] = counts.map((count, index) => {
      const from = edges[index]!;
      const to = from + binWidth;
      const height = (count / peak) * plotH;
      return {
        from,
        to,
        count,
        x: xAt(from) + 0.5,
        width: xAt(to) - xAt(from) - 1,
        y: MARGIN.top + plotH - height,
        height,
      };
    });

    return { bins, windows, domain, xAt, plotH };
  }, [analysis.errors, header.rate, header.windows]);

  if (!geometry) return null;
  const { bins, windows, domain, xAt, plotH } = geometry;
  const floor = MARGIN.top + plotH;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="block h-auto w-full"
        role="img"
        aria-label={t("analysis.histogramLabel")}
      >
        {windows.map(({ half, color }) => (
          <rect
            key={half}
            x={xAt(-half)}
            y={MARGIN.top}
            width={xAt(half) - xAt(-half)}
            height={plotH}
            fill={color}
            opacity={0.08}
          />
        ))}

        <line x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={floor} y2={floor} stroke={GRID} />
        <line
          x1={xAt(0)}
          x2={xAt(0)}
          y1={MARGIN.top}
          y2={floor}
          stroke={GRID}
          strokeWidth={1.5}
        />

        {bins.map(
          (bin) =>
            bin.count > 0 && (
              <rect
                key={bin.from}
                x={bin.x}
                y={bin.y}
                width={Math.max(bin.width, 1)}
                height={bin.height}
                fill={ACCENT}
                rx={1}
              />
            ),
        )}

        {/* One hit target per bin, wider than the bar and full height. */}
        {bins.map((bin) => (
          <rect
            key={`hit-${bin.from}`}
            x={bin.x - 0.5}
            y={MARGIN.top}
            width={bin.width + 1}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHover(bin)}
            onMouseLeave={() => setHover(null)}
          />
        ))}

        {[-domain, 0, domain].map((tick) => (
          <text
            key={tick}
            x={xAt(tick)}
            y={HEIGHT - 6}
            textAnchor={tick < 0 ? "start" : tick > 0 ? "end" : "middle"}
            {...AXIS_TEXT}
          >
            {tick > 0 ? `+${tick}` : tick}
          </text>
        ))}
      </svg>

      {hover && hover.count > 0 && (
        <ChartTooltip x={hover.x + hover.width / 2} y={hover.y} height={HEIGHT}>
          <div className="whitespace-nowrap font-mono tabular-nums text-ink">
            {signed(hover.from, 0)} to {signed(hover.to, 0)} ms
          </div>
          <div className="text-mute">{t("analysis.histogramCount", { n: hover.count })}</div>
        </ChartTooltip>
      )}
    </div>
  );
}
