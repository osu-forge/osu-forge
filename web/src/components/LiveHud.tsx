import { useMemo } from "react";
import type { ReplayHeader } from "@/lib/protocol";
import type { Key } from "@/i18n";

/**
 * What the play is at the playhead, not what it ended as.
 *
 * Watching a replay with only the final numbers on screen means the moment
 * accuracy fell is invisible while it is happening. The running totals here
 * are exact, not simulated: every judgement whose object is at or before the
 * clock, counted — the same numbers the results screen would show if the play
 * had ended right now. Nothing is projected forward, and there is
 * deliberately no running combo: reconstructing one exactly needs slider
 * parts the wire does not carry, and a plausible-but-wrong counter is worse
 * than none.
 *
 * The bar is the game's own accuracy meter, rebuilt from recorded data: the
 * last dozen hits as ticks on the judgement-window scale, newest brightest,
 * with a marker at their mean. A cloud of ticks sitting left of centre is a
 * player hitting early — visible while it happens, which is when it teaches.
 */

export interface LiveHudProps {
  header: ReplayHeader;
  /** Map time, milliseconds. */
  clock: number;
  t: (key: Key, values?: Record<string, string | number>) => string;
}

const RECENT = 12;
const BAR_WIDTH = 260;
const BAR_HEIGHT = 16;

const GRADE_TOKENS = [
  "var(--color-judge-miss)",
  "var(--color-judge-50)",
  "var(--color-judge-100)",
  "var(--color-judge-300)",
] as const;

interface Judged {
  time: number;
  error: number;
  grade: number;
}

export function LiveHud({ header, clock, t }: LiveHudProps) {
  // Prefix totals per object, once per replay, so the per-frame cost is a
  // binary search and four subtractions rather than a walk of the map.
  const totals = useMemo(() => {
    const n = header.objects.length;
    const times = new Float64Array(n);
    const grades = [new Uint32Array(n), new Uint32Array(n), new Uint32Array(n), new Uint32Array(n)];
    const judged: Judged[] = [];
    const counts = [0, 0, 0, 0];
    for (let i = 0; i < n; i++) {
      const object = header.objects[i]!;
      const judgement = header.judgements[i];
      // A judgement exists once it is decided: at the press for a circle, at
      // the end for a slider or spinner. Counting a slider at its start would
      // show its final grade while the ball is still travelling.
      times[i] = object.end;
      const grade = Math.min(3, Math.max(0, judgement?.grade ?? 3));
      counts[grade]! += 1;
      for (let g = 0; g < 4; g++) grades[g]![i] = counts[g]!;
      if (judgement && judgement.error !== null) {
        // The error bar is about presses, and a press happens at the head —
        // a slider's timing error is measured there whatever its end decides.
        judged.push({ time: object.t + judgement.error, error: judgement.error, grade });
      }
    }
    // Press times can land slightly out of object order — an early press on
    // one note after a late press on the last — and the scan below assumes
    // time order.
    judged.sort((a, b) => a.time - b.time);
    return { times, grades, judged };
  }, [header]);

  const n = totals.times.length;
  if (n === 0) return null;

  // Index of the last object at or before the clock.
  let low = 0;
  let high = n - 1;
  let at = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (totals.times[mid]! <= clock) {
      at = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  const so_far = at < 0 ? [0, 0, 0, 0] : totals.grades.map((grade) => grade[at]!);
  const judgedTotal = so_far[0]! + so_far[1]! + so_far[2]! + so_far[3]!;
  const accuracy =
    judgedTotal === 0
      ? 1
      : (300 * so_far[3]! + 100 * so_far[2]! + 50 * so_far[1]!) / (300 * judgedTotal);

  // The recent hits, and the spread of everything judged so far.
  let firstAfter = totals.judged.length;
  for (let i = 0; i < totals.judged.length; i++) {
    if (totals.judged[i]!.time > clock) {
      firstAfter = i;
      break;
    }
  }
  const recent = totals.judged.slice(Math.max(0, firstAfter - RECENT), firstAfter);
  const seen = totals.judged.slice(0, firstAfter);
  let unstable: number | null = null;
  if (seen.length >= 2) {
    const mean = seen.reduce((sum, hit) => sum + hit.error, 0) / seen.length;
    const variance =
      seen.reduce((sum, hit) => sum + (hit.error - mean) ** 2, 0) / (seen.length - 1);
    unstable = 10 * Math.sqrt(variance);
  }
  const recentMean =
    recent.length > 0 ? recent.reduce((sum, hit) => sum + hit.error, 0) / recent.length : null;

  const axis = header.windows[2] * 1.05;
  const xAt = (error: number) => ((error + axis) / (2 * axis)) * BAR_WIDTH;

  return (
    <>
      <div
        className="pointer-events-none absolute left-md top-md rounded-sm bg-canvas/60 px-md py-xs font-mono text-body-sm tabular-nums"
        aria-label={t("player.liveStats")}
      >
        <span className="text-ink">{(accuracy * 100).toFixed(2)}%</span>
        <span className="text-mute"> · UR {unstable === null ? "—" : unstable.toFixed(0)}</span>
        <span className="ml-sm">
          {([3, 2, 1, 0] as const).map((grade) => (
            <span key={grade} className="ml-xs" style={{ color: GRADE_TOKENS[grade] }}>
              {so_far[grade]}
            </span>
          ))}
        </span>
      </div>

      {recent.length > 0 && (
        <svg
          viewBox={`0 0 ${BAR_WIDTH} ${BAR_HEIGHT}`}
          width={BAR_WIDTH}
          height={BAR_HEIGHT}
          className="pointer-events-none absolute bottom-md left-1/2 -translate-x-1/2"
          role="img"
          aria-label={t("player.errorBar")}
        >
          {([2, 1, 0] as const).map((window) => (
            <rect
              key={window}
              x={xAt(-header.windows[window]!)}
              y={0}
              width={xAt(header.windows[window]!) - xAt(-header.windows[window]!)}
              height={BAR_HEIGHT}
              fill={GRADE_TOKENS[3 - window]}
              opacity={0.14}
            />
          ))}
          <rect x={BAR_WIDTH / 2 - 0.75} y={0} width={1.5} height={BAR_HEIGHT} fill="var(--color-ink)" opacity={0.7} />
          {recent.map((hit, index) => (
            <rect
              key={`${hit.time}-${index}`}
              x={xAt(hit.error) - 1}
              y={2}
              width={2}
              height={BAR_HEIGHT - 4}
              fill={GRADE_TOKENS[hit.grade]}
              opacity={0.25 + 0.75 * ((index + 1) / recent.length)}
            />
          ))}
          {recentMean !== null && (
            <path
              d={`M ${xAt(recentMean) - 3.5} 0 L ${xAt(recentMean) + 3.5} 0 L ${xAt(recentMean)} 5 Z`}
              fill="var(--color-ink)"
            />
          )}
        </svg>
      )}
    </>
  );
}
