import type { Corpus, CorpusAxis, CorpusBeatmap } from "@/lib/protocol";
import type { Key } from "@/i18n";

/**
 * What keeps happening, across every play rather than within one.
 *
 * The per-play panel can say "this happened"; only the corpus can say "this
 * keeps happening", and that is the sentence a player is actually asking for.
 * Three rules hold this panel together, each one a place a nicer-looking
 * version would quietly lie:
 *
 * **A refusal is content, not an empty state.** Below the corpus's own
 * thresholds the server sends a sentence saying exactly what is missing, and
 * the panel shows that sentence where the verdict would have been. "Not
 * enough data" and "nothing wrong" are opposite conclusions that look
 * identical as a blank page.
 *
 * **Not actionable is the headline answer, not a hidden row.** Two of the
 * three axes usually end in "no setting fixes this", and hiding them would
 * leave only the rows that say "change something" — a tool that can only
 * recommend will recommend when it should not.
 *
 * **A measurement is not advice until something with the authority says so.**
 * `health.may_recommend` stays false until an independent oracle has judged
 * these simulations object by object, and the panel says that on its face
 * rather than in a tooltip.
 */

export interface CorpusPanelProps {
  corpus: Corpus | null;
  t: (key: Key, values?: Record<string, string | number>) => string;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="text-display-xs tabular-nums">{value}</div>
      {hint && <div className="mt-xxs text-body-sm text-mute">{hint}</div>}
    </div>
  );
}

/** "+3.5" / "-2.1", matching how the per-play panel prints an error. */
function signed(value: number | null, digits = 1): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function AxisRow({ axis, t }: { axis: CorpusAxis; t: CorpusPanelProps["t"] }) {
  return (
    <li className="hairline-b py-md last:border-0">
      <div className="flex items-baseline justify-between gap-lg">
        <span className="text-body-md text-ink">{axis.verdict}</span>
        <span
          className={`shrink-0 rounded-pill border border-hairline px-sm text-body-sm ${
            axis.actionable ? "text-[color:var(--color-accent-breeze)]" : "text-mute"
          }`}
        >
          {axis.actionable ? t("corpus.actionable") : t("corpus.notActionable")}
        </span>
      </div>
      <div className="eyebrow mt-xxs">{axis.name}</div>
      {axis.detail && <p className="mt-xxs max-w-[70ch] text-body-sm text-mute">{axis.detail}</p>}
      {axis.evidence.map((line) => (
        <p key={line} className="mt-xxs font-mono text-body-sm tabular-nums text-mute">
          {line}
        </p>
      ))}
    </li>
  );
}

function BeatmapRow({ map, t }: { map: CorpusBeatmap; t: CorpusPanelProps["t"] }) {
  return (
    <li className="hairline-b py-md last:border-0">
      <div className="flex items-baseline justify-between gap-lg">
        <span className="truncate text-body-sm text-ink">{map.name}</span>
        <span className="shrink-0 font-mono text-body-sm tabular-nums text-body">
          {signed(map.mean)} ms
        </span>
      </div>
      <div className="eyebrow mt-xxs flex items-center gap-sm normal-case tracking-normal">
        <span className="tabular-nums">
          95% CI {signed(map.ci_low)} to {signed(map.ci_high)} ·{" "}
          {t("replays.count", { n: map.replays })}
        </span>
        {map.excludes_zero && (
          <span className="rounded-pill border border-hairline px-sm text-[color:var(--color-accent-sunset)]">
            {t("corpus.shifts")}
          </span>
        )}
      </div>
    </li>
  );
}

export function CorpusPanel({ corpus, t }: CorpusPanelProps) {
  if (!corpus) return null;

  const excluded = Object.entries(corpus.excluded);
  const thin = corpus.beatmaps.played - corpus.beatmaps.reported.length;

  return (
    <div className="flex flex-col gap-xl p-xl">
      {corpus.insufficient ? (
        // The refusal, where the verdict would have been — same size, same
        // place, so "cannot answer yet" is never mistaken for "nothing found".
        <p className="max-w-[62ch] text-body-lg text-body">{corpus.insufficient}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-lg sm:grid-cols-4">
            <Metric label={t("corpus.replays")} value={String(corpus.replays)} />
            <Metric label={t("corpus.sessions")} value={String(corpus.sessions)} />
            <Metric label={t("corpus.hits")} value={corpus.hits.toLocaleString()} />
            <Metric
              label={t("corpus.effective")}
              value={corpus.effective_hits === null ? "—" : corpus.effective_hits.toFixed(0)}
              hint={
                corpus.design_effect === null
                  ? undefined
                  : t("corpus.designEffect", { n: corpus.design_effect.toFixed(1) })
              }
            />
          </div>

          {!corpus.health.may_recommend && (
            <p className="rounded-sm border border-hairline p-md text-body-sm text-mute">
              <span className="text-ink">{t("corpus.measurementOnly")}</span>{" "}
              {corpus.health.blockers.join(" ")}
            </p>
          )}

          <section>
            <h2 className="eyebrow mb-sm">{t("corpus.axes")}</h2>
            <ul className="m-0 list-none p-0">
              {corpus.axes.map((axis) => (
                <AxisRow key={axis.name} axis={axis} t={t} />
              ))}
            </ul>
          </section>

          {corpus.beatmaps.reported.length > 0 && (
            <section>
              <h2 className="eyebrow mb-sm">{t("corpus.perMap")}</h2>
              {corpus.beatmaps.reading && (
                <p className="mb-md max-w-[70ch] text-body-sm text-body">
                  {corpus.beatmaps.reading}
                </p>
              )}
              <ul className="m-0 list-none p-0">
                {corpus.beatmaps.reported.map((map) => (
                  <BeatmapRow key={map.name} map={map} t={t} />
                ))}
              </ul>
              {thin > 0 && (
                <p className="mt-sm text-body-sm text-mute">
                  {t("corpus.thinMaps", { n: thin })}
                </p>
              )}
            </section>
          )}
        </>
      )}

      {excluded.length > 0 && (
        <section>
          <h2 className="eyebrow mb-sm">{t("corpus.excluded", { n: excluded.length })}</h2>
          <ul className="m-0 list-none p-0">
            {excluded.map(([name, reason]) => (
              <li key={name} className="hairline-b py-md text-body-sm last:border-0">
                <span className="font-mono text-ink">{name}</span>
                <p className="mt-xxs max-w-[70ch] text-mute">{reason}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
