import { useMemo } from "react";
import { sampleAt, type Samples } from "@/lib/protocol";

/**
 * Which keys are down right now, and how many presses each has made so far.
 *
 * The playfield marks a press as a ring on the cursor trail, but "which
 * finger" is invisible there — and alternation is exactly what a viewer
 * scrubs a stream to check. This is the standard key overlay: one chip per
 * input, lit while held, with a running press count that advances as the
 * play does.
 *
 * The mask is osu!'s own: bit 0 M1, bit 1 M2, bit 2 K1, bit 3 K2, and a
 * keyboard press also raises its mouse bit — so M1/M2 only count as
 * themselves when their K bit is off, or every keyboard player would appear
 * to be pressing four things. Mouse chips are hidden entirely for a replay
 * that never pressed them; two dead chips would just be noise beside the
 * two that matter.
 */

export interface KeyOverlayProps {
  samples: Samples;
  /** Map time, milliseconds. */
  clock: number;
}

interface Input {
  label: string;
  held: (mask: number) => boolean;
}

const INPUTS: Input[] = [
  { label: "K1", held: (mask) => (mask & 4) !== 0 },
  { label: "K2", held: (mask) => (mask & 8) !== 0 },
  { label: "M1", held: (mask) => (mask & 1) !== 0 && (mask & 4) === 0 },
  { label: "M2", held: (mask) => (mask & 2) !== 0 && (mask & 8) === 0 },
];

export function KeyOverlay({ samples, clock }: KeyOverlayProps) {
  // Presses up to each sample, once per replay. Counting on every clock tick
  // would walk the whole recording sixty times a second; four prefix arrays
  // make the running count an index instead.
  const prefixes = useMemo(() => {
    const n = samples.keys.length;
    return INPUTS.map(({ held }) => {
      const prefix = new Uint32Array(n);
      let count = 0;
      let before = false;
      for (let i = 0; i < n; i++) {
        const now = held(samples.keys[i]!);
        if (now && !before) count += 1;
        before = now;
        prefix[i] = count;
      }
      return prefix;
    });
  }, [samples]);

  if (samples.keys.length === 0) return null;
  const at = sampleAt(samples.t, clock);
  const mask = samples.keys[at]!;

  return (
    <div className="flex items-center gap-xs">
      {INPUTS.map(({ label, held }, index) => {
        const presses = prefixes[index]!;
        // A mouse chip earns its place only by being used at some point.
        if (label.startsWith("M") && presses[presses.length - 1] === 0) return null;
        const down = held(mask);
        return (
          <span
            key={label}
            className={`rounded-sm border px-sm py-xs font-mono text-body-sm tabular-nums ${
              down
                ? "border-[color:var(--color-accent-breeze)] text-[color:var(--color-accent-breeze)]"
                : "border-hairline text-mute"
            }`}
          >
            {label} {presses[at]}
          </span>
        );
      })}
    </div>
  );
}
