/**
 * The handful of ways a number is written down on this page.
 *
 * Each of these was three or four copies before it was one, and the copies had
 * already drifted: a signed millisecond printed with a `+` in one panel and
 * without it in another says the two panels are measuring different things,
 * which they are not.
 */

/**
 * `m:ss`, clamped so a clock sitting before the first sample reads `0:00`.
 *
 * Elapsed time, never a duration with hours in it: a replay is minutes long,
 * and `00:03:41` in a transport is three fields to read where two would do.
 */
export function stamp(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * `+3.5` / `-2.1`, and `—` for a number that does not exist.
 *
 * The sign is always drawn because these are errors around zero, and an
 * unsigned `3.5 ms` beside a `-2.1 ms` reads as the larger of the two rather
 * than as the opposite direction.
 */
export function signed(value: number | null, digits = 1): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/**
 * `MM-DD` out of an ISO timestamp.
 *
 * The year is dropped rather than formatted away: everything dated on this page
 * is from the sessions being looked at, and a column of identical years is four
 * characters of noise per row. Sliced rather than parsed, so a timestamp is
 * never quietly moved into another day by a timezone conversion nothing asked
 * for.
 */
export function monthDay(iso: string): string {
  return iso.slice(5, 10);
}

/**
 * `HH:MM` off the reader's own clock.
 *
 * For "this answer arrived at", which is only ever compared against the clock
 * on the wall in front of whoever is looking — so it is local time, and it is
 * padded because a column that jumps between `9:05` and `10:05` is a column
 * that moves while being read.
 */
export function hourMinute(at: Date): string {
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}
