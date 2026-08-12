import { accuracyOf, modNames, type Entry } from "@/lib/protocol";
import { monthDay, signed } from "@/lib/format";
import type { Translator } from "@/i18n";

/**
 * One play, in the six fields that identify it.
 *
 * The overview shows the newest one and a live session shows every one that has
 * arrived; they are the same card because they answer the same question, and a
 * play that looked different on two pages would read as two plays.
 *
 * A link into the viewer, not a button. The viewer is another document, so this
 * is a navigation whatever it is dressed as — and written as an anchor it can
 * be opened in a new tab, which is how someone compares two plays.
 *
 * Everything here is read off the header rather than the analysis, except the
 * two numbers that only exist there. A play the simulation could not reproduce
 * still has an artist, a title and an accuracy from the game's own counts, and
 * dropping the card for a missing analysis would hide the play whose analysis
 * failed — which is the one worth knowing about.
 */
export function PlayCard({ entry, t }: { entry: Entry; t: Translator }) {
  const { beatmap, counts, mods, analysis } = entry.header;
  const badges = modNames(mods);

  return (
    <a
      href={`/replays/#r=${encodeURIComponent(entry.name)}`}
      className="hairline-b block py-md last:border-0 hover:text-ink-hover"
    >
      <div className="flex items-baseline justify-between gap-lg">
        <span className="truncate text-body-md text-ink">
          {beatmap.artist} — {beatmap.title}
        </span>
        {badges.length > 0 && (
          <span className="shrink-0 font-mono text-body-sm text-[color:var(--color-accent-breeze)]">
            {badges.join(" ")}
          </span>
        )}
      </div>
      <div className="eyebrow mt-xxs flex flex-wrap items-center gap-sm normal-case tracking-normal">
        <span className="truncate">[{beatmap.version}]</span>
        <span className="tabular-nums">{(accuracyOf(counts) * 100).toFixed(2)}%</span>
        {analysis?.unstable_rate != null && (
          <span className="tabular-nums">
            {t("analysis.unstableRate")} {analysis.unstable_rate.toFixed(1)}
          </span>
        )}
        {analysis?.mean_error != null && (
          <span className="tabular-nums">{signed(analysis.mean_error)} ms</span>
        )}
        {analysis && <span className="tabular-nums">{monthDay(analysis.played_at)}</span>}
      </div>
    </a>
  );
}
