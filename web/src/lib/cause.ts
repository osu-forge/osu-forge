import type { Break, ReplayHeader } from "@/lib/protocol";

/**
 * Why a break happened, read off what was recorded.
 *
 * This is the reason the page exists. Playing a replay back is something osu!
 * already does; saying which mistake was made is not.
 *
 * Every label carries the number it came from. A verdict without its evidence
 * is a guess wearing a uniform, and the number is usually the more useful half
 * — "late by 58 ms" tells a player what to change in a way "too late" does not.
 *
 * # What is deliberately not claimed
 *
 * A dropped slider has two very different causes: the key came up while the
 * cursor was still tracking, or the cursor left the follow area while the key
 * was held. Telling them apart needs per-frame tracking that is not built yet,
 * so a dropped slider reports how much of it was collected and stops there.
 * Guessing between the two would be the most useful-sounding wrong answer this
 * page could give.
 */

export type CauseKind =
  | "never-pressed"
  | "late"
  | "early"
  | "edge"
  | "slider-dropped"
  | "slider-head-missed"
  | "unknown";

export interface Cause {
  kind: CauseKind;
  /** One line, already carrying its own numbers. */
  text: string;
  /** How far outside the window, in milliseconds, when that is the story. */
  ms?: number;
}

/** Distance from centre, in radii, past which a hit counts as an edge hit. */
const EDGE = 0.85;

export function causeOf(item: Break, header: ReplayHeader): Cause {
  const [, , window50] = header.windows;

  if (item.kind === "slider") {
    const collected = item.parts_collected;
    const total = item.parts_total;
    if (collected === 0 && total !== null) {
      return {
        kind: "slider-head-missed",
        text: "the head was never hit, so nothing after it could be tracked — an aim problem rather than a slider one",
      };
    }
    if (collected !== null && total !== null) {
      return {
        kind: "slider-dropped",
        text:
          `dropped after ${collected} of ${total} scoring points. ` +
          "Whether the key came up or the cursor left the follow area needs per-frame tracking, which is not built yet",
      };
    }
    return { kind: "unknown", text: "dropped, with no part count recorded" };
  }

  if (item.error === null) {
    return {
      kind: "never-pressed",
      text: `no press within ${window50} ms of it — nothing was aimed at this object`,
    };
  }

  const overshoot = Math.abs(item.error) - window50;
  if (overshoot > 0) {
    return {
      kind: item.error > 0 ? "late" : "early",
      ms: Math.round(overshoot),
      text:
        `pressed ${Math.abs(item.error)} ms ${item.error > 0 ? "late" : "early"}, ` +
        `which is ${Math.round(overshoot)} ms outside this map's ${window50} ms window`,
    };
  }

  if (item.aim_error !== null && item.aim_error >= EDGE) {
    return {
      kind: "edge",
      text: `hit ${(item.aim_error * 100).toFixed(0)}% of the way to the edge of the circle`,
    };
  }

  return {
    kind: item.error > 0 ? "late" : "early",
    ms: Math.abs(item.error),
    text: `${Math.abs(item.error)} ms ${item.error > 0 ? "late" : "early"}, inside the window`,
  };
}
