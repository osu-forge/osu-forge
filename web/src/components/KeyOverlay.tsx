import { useMemo } from "react";
import { sampleAt, type Samples } from "@/lib/protocol";
import { INPUTS, pressCountAt, pressesOf } from "@/lib/keys";

/**
 * Which keys are down right now, and how many presses each has made so far.
 *
 * The playfield marks a press as a ring on the cursor trail, but "which
 * finger" is invisible there — and alternation is exactly what a viewer
 * scrubs a stream to check. This is the standard key overlay: one chip per
 * input, lit while held, with a running press count that advances as the
 * play does.
 *
 * The mask semantics live in `lib/keys`, shared with the keyboard panel, so
 * "what counts as a press" is decided once. Mouse chips are hidden entirely
 * for a replay that never pressed them; two dead chips would just be noise
 * beside the two that matter.
 */

export interface KeyOverlayProps {
  samples: Samples;
  /** Map time, milliseconds. */
  clock: number;
}

export function KeyOverlay({ samples, clock }: KeyOverlayProps) {
  const presses = useMemo(() => pressesOf(samples), [samples]);

  if (samples.keys.length === 0) return null;
  const at = sampleAt(samples.t, clock);
  const mask = samples.keys[at]!;

  return (
    <div className="flex items-center gap-xs">
      {INPUTS.map(({ name, label, held }) => {
        const made = presses[name];
        // A mouse chip earns its place only by being used at some point.
        if (label.startsWith("M") && made.length === 0) return null;
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
            {label} {pressCountAt(made, at)}
          </span>
        );
      })}
    </div>
  );
}
