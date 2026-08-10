import type { Samples } from "@/lib/protocol";

/**
 * The presses, read off the recorded samples.
 *
 * Everything on this page that talks about fingers reads from here, so the
 * mask semantics live in exactly one place. The mask is osu!'s own: bit 0 M1,
 * bit 1 M2, bit 2 K1, bit 3 K2, bit 4 smoke — and a keyboard press also raises
 * its mouse bit, so M1/M2 only count as themselves while their K bit is off.
 * Anything that asks "is a button down" must ask per bit; `mask !== 0` is true
 * for smoke alone.
 *
 * The times are map milliseconds, sampled at whatever rate the game rendered
 * at — about sixty a second. An edge is recorded at the first sample at or
 * after the physical event, so each one carries up to one sample gap of error
 * and that gap is kept with the press rather than thrown away. A press on the
 * very first sample has no previous sample to bound it: its gap is `null`,
 * meaning unbounded, not zero. Sample times step backwards at rare seams
 * around the lead-in exit, and a non-positive gap bounds nothing either, so it
 * is reported the same way.
 *
 * This module deliberately keeps the web's instantaneous exclusion rule rather
 * than the Python side's previous-frame attribution. They agree on every
 * ordinary stream; they differ only when a key goes down inside a physical
 * mouse hold, and there the page should show what the mask said at that
 * instant, because that is what the key chips are lit from.
 */

export type InputName = "k1" | "k2" | "m1" | "m2";

export interface Input {
  name: InputName;
  label: string;
  held: (mask: number) => boolean;
}

export const INPUTS: readonly Input[] = [
  { name: "k1", label: "K1", held: (mask) => (mask & 4) !== 0 },
  { name: "k2", label: "K2", held: (mask) => (mask & 8) !== 0 },
  { name: "m1", label: "M1", held: (mask) => (mask & 1) !== 0 && (mask & 4) === 0 },
  { name: "m2", label: "M2", held: (mask) => (mask & 2) !== 0 && (mask & 8) === 0 },
];

export interface Press {
  /** Sample index of the down edge. */
  index: number;
  /** Map milliseconds at the down edge. */
  time: number;
  /** Sample index of the release, or `null` when the input was still down at
   *  the end of the recording. */
  releaseIndex: number | null;
  releaseTime: number | null;
  /** Sample gap before the down edge, map milliseconds — the width of the
   *  window the physical press actually happened in. `null` is unbounded. */
  gap: number | null;
}

export type Presses = Record<InputName, Press[]>;

/** One tap in the merged stream, whichever input made it. */
export interface Tap {
  input: InputName;
  time: number;
}

/** The wait between two consecutive taps. */
export interface Interval {
  /** Map milliseconds of the later tap — where this belongs on the timeline. */
  time: number;
  /** Map milliseconds waited. */
  map: number;
  /** Real milliseconds waited: what the fingers did, rate divided out. */
  real: number;
  /** Whether the later tap used a different input than the one before it. */
  alternated: boolean;
}

function gapBefore(t: Int32Array, index: number): number | null {
  if (index === 0) return null;
  const gap = t[index]! - t[index - 1]!;
  return gap > 0 ? gap : null;
}

/** Every press each input made, in recording order. */
export function pressesOf(samples: Samples): Presses {
  const found: Presses = { k1: [], k2: [], m1: [], m2: [] };
  const n = samples.keys.length;
  for (const { name, held } of INPUTS) {
    const list = found[name];
    let before = false;
    let open: Press | null = null;
    for (let i = 0; i < n; i++) {
      const now = held(samples.keys[i]!);
      if (now && !before) {
        open = {
          index: i,
          time: samples.t[i]!,
          releaseIndex: null,
          releaseTime: null,
          gap: gapBefore(samples.t, i),
        };
        list.push(open);
      } else if (!now && before && open) {
        open.releaseIndex = i;
        open.releaseTime = samples.t[i]!;
        open = null;
      }
      before = now;
    }
  }
  return found;
}

/** How many of these presses had happened by `index`. */
export function pressCountAt(presses: readonly Press[], index: number): number {
  let low = 0;
  let high = presses.length - 1;
  let count = 0;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (presses[mid]!.index <= index) {
      count = mid + 1;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return count;
}

/** How long each completed press was held, real milliseconds.
 *
 * A press still down when the recording stopped has no duration to give, so it
 * is left out rather than measured against the end of the file.
 */
export function holdsOf(presses: readonly Press[], rate: number): number[] {
  const held: number[] = [];
  for (const press of presses) {
    if (press.releaseTime === null) continue;
    const span = press.releaseTime - press.time;
    if (span > 0) held.push(span / rate);
  }
  return held;
}

/** Every press by every input that made one, merged into one ordered stream. */
export function tapsOf(presses: Presses): Tap[] {
  const taps: Tap[] = [];
  for (const { name } of INPUTS) {
    for (const press of presses[name]) taps.push({ input: name, time: press.time });
  }
  taps.sort((a, b) => a.time - b.time);
  return taps;
}

/** The waits between consecutive taps.
 *
 * Sample times are not guaranteed to ascend — the recording steps backwards a
 * millisecond or two at rare seams, and two inputs can land on one sample — so
 * pairs that would produce a zero or negative wait are dropped instead of
 * turned into an impossible tapping speed.
 */
export function intervalsOf(taps: readonly Tap[], rate: number): Interval[] {
  const intervals: Interval[] = [];
  for (let i = 1; i < taps.length; i++) {
    const map = taps[i]!.time - taps[i - 1]!.time;
    if (map <= 0) continue;
    intervals.push({
      time: taps[i]!.time,
      map,
      real: map / rate,
      alternated: taps[i]!.input !== taps[i - 1]!.input,
    });
  }
  return intervals;
}

/** The value at `q` of an already-sorted ascending list. */
export function quantile(sorted: readonly number[], q: number): number {
  if (sorted.length === 0) return 0;
  const at = Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))));
  return sorted[at]!;
}

export function median(values: readonly number[]): number {
  return quantile([...values].sort((a, b) => a - b), 0.5);
}

/** The quickest the player ever sustained, as a mean over `span` waits.
 *
 * A single lucky pair of frames is noise; a run of them is a burst. `null`
 * when the play never held the pace for that many taps in a row.
 */
export function fastestBurst(intervals: readonly Interval[], span = 8): number | null {
  if (intervals.length < span) return null;
  let running = 0;
  for (let i = 0; i < span; i++) running += intervals[i]!.real;
  let best = running;
  for (let i = span; i < intervals.length; i++) {
    running += intervals[i]!.real - intervals[i - span]!.real;
    if (running < best) best = running;
  }
  return best / span;
}

/** The share of tap pairs that changed hands. 1 is perfect alternation. */
export function alternationShare(intervals: readonly Interval[]): number | null {
  if (intervals.length === 0) return null;
  let changed = 0;
  for (const interval of intervals) if (interval.alternated) changed += 1;
  return changed / intervals.length;
}

/** Streaming BPM a wait of `ms` real milliseconds corresponds to at 1/4 snap. */
export function snapBpm(ms: number): number {
  return ms > 0 ? 15000 / ms : 0;
}
