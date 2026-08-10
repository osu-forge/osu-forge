import { useMemo, useState } from "react";
import type { ReplayHeader, Samples } from "@/lib/protocol";
import {
  INPUTS,
  alternationShare,
  fastestBurst,
  holdsOf,
  intervalsOf,
  median,
  pressesOf,
  quantile,
  snapBpm,
  tapsOf,
  type Interval,
} from "@/lib/keys";
import type { Key } from "@/i18n";

/**
 * What the hands did, counted off the recorded samples.
 *
 * Nothing here is estimated. Every number is a count or a difference of two
 * recorded timestamps, which is why the panel can say them plainly and then
 * say, once, exactly how coarse the timestamps are. The one derived figure —
 * streaming BPM — is a unit conversion of a wait, not a claim about what the
 * map was snapped to, so it is only ever offered beside the millisecond it
 * came from.
 *
 * Waits are shown in real milliseconds. Under Double Time the map clock runs
 * 1.5x, and a stream that reads 90 map-ms was tapped at 60 real-ms; showing
 * the map figure would tell a player their fingers are slower than they are.
 *
 * The chart's y axis stops at the 99th percentile because a single pause
 * between two sections is longer than every stream in the play put together,
 * and letting it set the scale would flatten the part worth looking at. The
 * waits above the cap are still drawn, pinned to the top edge and faded, so
 * they are visibly present rather than quietly dropped.
 */

export interface KeysPanelProps {
  samples: Samples;
  header: ReplayHeader;
  t: (key: Key, values?: Record<string, string | number>) => string;
  onSeek?: (time: number) => void;
}

const WIDTH = 720;
const HEIGHT = 240;
const MARGIN = { top: 14, right: 12, bottom: 26, left: 48 } as const;

const DOT = "var(--color-accent-breeze)";
const GRID = "var(--color-hairline)";
const INK_MUTE = "var(--color-mute)";

/** The smallest round number at or above `value`, for an axis top. */
function niceCeil(value: number): number {
  const power = 10 ** Math.floor(Math.log10(Math.max(value, 1)));
  for (const step of [1, 1.5, 2, 3, 5, 7.5]) {
    if (value <= step * power) return step * power;
  }
  return 10 * power;
}

/** `m:ss` of real elapsed time, matching what the transport counts. */
function stamp(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

interface Placed {
  interval: Interval;
  x: number;
  y: number;
  over: boolean;
}

export function KeysPanel({ samples, header, t, onSeek }: KeysPanelProps) {
  const [hover, setHover] = useState<Placed | null>(null);

  const derived = useMemo(() => {
    const rate = header.rate || 1;
    const presses = pressesOf(samples);
    const rows = INPUTS.filter((input) => presses[input.name].length > 0).map((input) => {
      const made = presses[input.name];
      const holds = holdsOf(made, rate);
      return {
        label: input.label,
        count: made.length,
        hold: holds.length > 0 ? median(holds) : null,
      };
    });

    const intervals = intervalsOf(tapsOf(presses), rate);
    const k1 = presses.k1.length;
    const k2 = presses.k2.length;
    const balance = k1 + k2 > 0 ? k1 / (k1 + k2) : null;

    return {
      rate,
      rows,
      intervals,
      balance,
      middle: intervals.length > 0 ? median(intervals.map((one) => one.real)) : null,
      burst: fastestBurst(intervals),
      alternation: alternationShare(intervals),
    };
  }, [samples, header.rate]);

  const geometry = useMemo(() => {
    const { intervals, rate, middle } = derived;
    const n = samples.t.length;
    if (intervals.length === 0 || n < 2 || middle === null) return null;

    const first = samples.t[0]!;
    const span = samples.t[n - 1]! - first;
    if (span <= 0) return null;

    const sorted = intervals.map((one) => one.real).sort((a, b) => a - b);
    const cap = niceCeil(quantile(sorted, 0.99));

    const plotW = WIDTH - MARGIN.left - MARGIN.right;
    const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
    const xAt = (time: number) => MARGIN.left + ((time - first) / span) * plotW;
    const yAt = (ms: number) => MARGIN.top + (1 - Math.min(ms, cap) / cap) * plotH;

    const placed: Placed[] = intervals.map((interval) => ({
      interval,
      x: xAt(interval.time),
      y: yAt(interval.real),
      over: interval.real > cap,
    }));

    // One path for all the ticks rather than one element each: a full map is
    // thousands of taps, and thousands of nodes in the aside costs more than
    // the picture is worth.
    const tick = (list: Placed[]) =>
      list.map(({ x, y }) => `M${x.toFixed(1)} ${(y - 1.3).toFixed(1)}v2.6`).join("");

    return {
      cap,
      first,
      span,
      plotW,
      plotH,
      placed,
      inside: tick(placed.filter((one) => !one.over)),
      above: tick(placed.filter((one) => one.over)),
      overflow: placed.filter((one) => one.over).length,
      medianY: yAt(middle),
      rate,
      last: samples.t[n - 1]!,
    };
  }, [derived, samples]);

  if (derived.rows.length === 0) return null;

  function locate(event: React.MouseEvent<SVGSVGElement>): Placed | null {
    if (!geometry) return null;
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0) return null;
    const local = ((event.clientX - box.left) / box.width) * WIDTH;
    let best: Placed | null = null;
    let distance = Infinity;
    for (const one of geometry.placed) {
      const away = Math.abs(one.x - local);
      if (away < distance) {
        distance = away;
        best = one;
      }
    }
    return distance <= 12 ? best : null;
  }

  const { rows, middle, burst, alternation, balance } = derived;
  const pace = (ms: number) =>
    t("keys.pace", { ms: Math.round(ms), bpm: Math.round(snapBpm(ms)) });

  return (
    <div className="hairline-t flex flex-col gap-xl p-xl">
      <section>
        <h2 className="eyebrow mb-sm">{t("keys.inputs")}</h2>
        <ul className="m-0 flex list-none flex-wrap gap-xl p-0">
          {rows.map((row) => (
            <li key={row.label}>
              <div className="font-mono text-body-sm tabular-nums text-ink">
                {row.label} {t("keys.pressCount", { n: row.count })}
              </div>
              <div className="mt-xxs text-body-sm text-mute">
                {row.hold === null
                  ? t("keys.holdUnknown")
                  : t("keys.hold", { ms: Math.round(row.hold) })}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {geometry && middle !== null && (
        <section>
          <h2 className="eyebrow mb-sm">{t("keys.intervals")}</h2>
          <p className="mb-md max-w-[70ch] text-body-sm text-mute">{t("keys.intervalsHint")}</p>

          <div className="relative">
            <svg
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              className={`block h-auto w-full ${onSeek ? "cursor-pointer" : ""}`}
              role="img"
              aria-label={t("keys.chartLabel")}
              onMouseMove={(event) => setHover(locate(event))}
              onMouseLeave={() => setHover(null)}
              onClick={(event) => {
                const found = locate(event);
                if (found) onSeek?.(found.interval.time);
              }}
            >
              <line
                x1={MARGIN.left}
                x2={WIDTH - MARGIN.right}
                y1={MARGIN.top + geometry.plotH}
                y2={MARGIN.top + geometry.plotH}
                stroke={GRID}
              />
              <line
                x1={MARGIN.left}
                x2={WIDTH - MARGIN.right}
                y1={MARGIN.top}
                y2={MARGIN.top}
                stroke={GRID}
              />
              {/* The middle of the distribution, so a run reads as above or
                  below this play's own pace without arithmetic. */}
              <line
                x1={MARGIN.left}
                x2={WIDTH - MARGIN.right}
                y1={geometry.medianY}
                y2={geometry.medianY}
                stroke={GRID}
                strokeDasharray="3 4"
              />

              <path d={geometry.inside} stroke={DOT} strokeWidth={1.6} strokeLinecap="round" />
              <path
                d={geometry.above}
                stroke={DOT}
                strokeWidth={1.6}
                strokeLinecap="round"
                opacity={0.35}
              />

              {hover && (
                <circle cx={hover.x} cy={hover.y} r={3} fill="none" stroke={DOT} />
              )}

              {[
                { value: geometry.cap, y: MARGIN.top + 4 },
                { value: 0, y: MARGIN.top + geometry.plotH },
              ].map(({ value, y }) => (
                <text
                  key={value}
                  x={MARGIN.left - 6}
                  y={y}
                  textAnchor="end"
                  fontSize={11}
                  fill={INK_MUTE}
                  fontFamily="var(--font-mono, monospace)"
                >
                  {value}
                </text>
              ))}

              {[
                { at: geometry.first, anchor: "start" as const, x: MARGIN.left },
                { at: geometry.last, anchor: "end" as const, x: WIDTH - MARGIN.right },
              ].map(({ at, anchor, x }) => (
                <text
                  key={anchor}
                  x={x}
                  y={HEIGHT - 6}
                  textAnchor={anchor}
                  fontSize={11}
                  fill={INK_MUTE}
                  fontFamily="var(--font-mono, monospace)"
                >
                  {stamp((at - geometry.first) / geometry.rate)}
                </text>
              ))}
            </svg>

            {hover && (
              <div
                className="pointer-events-none absolute z-10 rounded-sm border border-hairline bg-canvas-card px-md py-xs text-body-sm"
                style={{
                  left: `${(hover.x / WIDTH) * 100}%`,
                  top: `${(hover.y / HEIGHT) * 100}%`,
                  // The panel is narrow enough that a centred tooltip on an
                  // early or late tap would hang off the aside and be clipped,
                  // so near the edges it hangs inward instead.
                  transform: `translate(${
                    hover.x < WIDTH * 0.25 ? "0" : hover.x > WIDTH * 0.75 ? "-100%" : "-50%"
                  }, calc(-100% - 8px))`,
                }}
              >
                <div className="whitespace-nowrap font-mono tabular-nums text-ink">
                  {stamp((hover.interval.time - geometry.first) / geometry.rate)} ·{" "}
                  {t("keys.tooltipInterval", { ms: Math.round(hover.interval.real) })}
                </div>
                <div className="whitespace-nowrap text-mute">
                  {t("keys.tooltipBpm", { bpm: Math.round(snapBpm(hover.interval.real)) })}
                </div>
              </div>
            )}
          </div>

          {geometry.overflow > 0 && (
            <p className="mt-sm text-body-sm text-mute">
              {t("keys.capped", { n: geometry.overflow, ms: geometry.cap })}
            </p>
          )}

          <div className="mt-lg grid grid-cols-2 gap-lg">
            <div>
              <div className="eyebrow">{t("keys.median")}</div>
              <div className="font-mono text-body-md tabular-nums">{pace(middle)}</div>
            </div>
            {burst !== null && (
              <div>
                <div className="eyebrow">{t("keys.burst")}</div>
                <div className="font-mono text-body-md tabular-nums">{pace(burst)}</div>
              </div>
            )}
            {alternation !== null && (
              <div>
                <div className="eyebrow">{t("keys.alternation")}</div>
                <div className="font-mono text-body-md tabular-nums">
                  {(alternation * 100).toFixed(0)}%
                </div>
              </div>
            )}
            {balance !== null && (
              <div>
                <div className="eyebrow">{t("keys.balance")}</div>
                <div className="font-mono text-body-md tabular-nums">
                  {(balance * 100).toFixed(0)}%
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      <p className="max-w-[70ch] text-body-sm text-mute">{t("keys.samplingHint")}</p>
    </div>
  );
}
