import type {
  Corpus,
  CorpusAxis,
  CorpusBeatmap,
  CorpusProgress,
  CorpusVerification,
} from "@/lib/protocol";
import { ProgressChart } from "@/components/ProgressChart";
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
 * identical as a blank page. The refusal replaces the diagnosis and not the
 * verification: the two count what they need differently, and the window in
 * which the corpus is too thin to diagnose is exactly the window just after a
 * change, where a CONTRADICTED verdict is the most useful sentence on the
 * page.
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
  /** When this answer arrived, so a page left open shows whether the corpus
   *  it is looking at is from just now or from before lunch. */
  updatedAt?: Date | null;
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

const VERDICT_COLOUR: Record<string, string> = {
  contradicted: "text-[color:var(--color-judge-miss)]",
  confirmed: "text-[color:var(--color-accent-breeze)]",
};

/**
 * Whether the change did what it predicted — the only measurement on this page
 * that can come out wrong, and the reason the rest of it is not self-confirming.
 *
 * Mounted from two places, because it outlives both of the refusals around it:
 * beside the shift when the corpus can be diagnosed, and beside the corpus
 * refusal when it cannot. It carries its own heading and no outer spacing, so
 * whichever caller mounts it decides where it sits.
 *
 * No interval here, deliberately. The shift above already puts one on this same
 * difference, and the two are not the same number: where the bootstrap route
 * wins they are drawn independently and disagree in the second decimal. Two
 * lines in one section, both labelled 95% CI and disagreeing about one
 * difference, is worse than one line. The payload still carries
 * `ci_low`/`ci_high` for a reader that wants the whole record.
 */
function VerificationBlock({
  verification,
  t,
}: {
  verification: CorpusVerification;
  t: CorpusPanelProps["t"];
}) {
  return (
    <section>
      <h3 className="eyebrow mb-sm">{t("corpus.verification")}</h3>
      <div className="flex flex-wrap items-baseline gap-sm">
        <span
          className={`rounded-pill border border-hairline px-sm text-body-sm ${
            VERDICT_COLOUR[verification.verdict] ?? "text-mute"
          }`}
        >
          {verification.verdict}
        </span>
        {verification.predicted !== null && verification.difference !== null && (
          <span className="font-mono text-body-sm tabular-nums text-body">
            {t("corpus.predictedObserved", {
              p: signed(verification.predicted),
              o: signed(verification.difference),
            })}
          </span>
        )}
      </div>
      <p className="mt-sm max-w-[70ch] text-body-sm text-mute">{verification.reason}</p>
    </section>
  );
}

function ProgressSection({
  progress,
  t,
}: {
  progress: CorpusProgress;
  t: CorpusPanelProps["t"];
}) {
  const shift = progress.shift;
  if (progress.points.length === 0 && !progress.insufficient) return null;

  return (
    <section>
      <h2 className="eyebrow mb-sm">{t("progress.heading")}</h2>
      {progress.boundary && (
        <p className="mb-md max-w-[70ch] text-body-sm text-mute">{progress.boundary.label}</p>
      )}
      {progress.points.length > 0 && <ProgressChart progress={progress} t={t} />}

      {shift ? (
        <div className="mt-md">
          {/* The verdict leads; the sides that produced it follow, each with
              the interval and counts that make it checkable. */}
          <p className="max-w-[70ch] text-body-md text-ink">{shift.verdict}</p>
          <div className="mt-sm flex flex-col gap-xxs">
            {(["before", "after"] as const).map((era) => {
              const side = era === "before" ? shift.before : shift.after;
              return (
                <div key={era} className="eyebrow flex items-baseline gap-sm normal-case tracking-normal">
                  <span className="w-[7ch] shrink-0 uppercase tracking-[0.08em]">
                    {t(era === "before" ? "progress.before" : "progress.after")}
                  </span>
                  <span className="font-mono tabular-nums text-body">
                    {side.mean === null ? "—" : `${side.mean >= 0 ? "+" : ""}${side.mean.toFixed(1)} ms`}{" "}
                    ({side.ci_low === null ? "—" : `${side.ci_low >= 0 ? "+" : ""}${side.ci_low.toFixed(1)}`}{" "}
                    to {side.ci_high === null ? "—" : `${side.ci_high >= 0 ? "+" : ""}${side.ci_high.toFixed(1)}`})
                  </span>
                  <span>{t("progress.sideCounts", { n: side.replays, s: side.sessions })}</span>
                </div>
              );
            })}
          </div>
          {shift.spread_before !== null && shift.spread_after !== null && (
            <p className="mt-sm max-w-[70ch] text-body-sm text-mute">
              {t("progress.spreadNote", {
                b: shift.spread_before.toFixed(1),
                a: shift.spread_after.toFixed(1),
              })}
            </p>
          )}
        </div>
      ) : (
        progress.insufficient && (
          <p className="mt-md max-w-[70ch] text-body-sm text-mute">{progress.insufficient}</p>
        )
      )}

      {/* Beside the shift rather than inside it: the two refusals count
          sessions their own way, so a verdict can exist where progress
          declined to draw one, and the other way round. */}
      {progress.verification && (
        <div className="mt-lg">
          <VerificationBlock verification={progress.verification} t={t} />
        </div>
      )}
    </section>
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

export function CorpusPanel({ corpus, updatedAt, t }: CorpusPanelProps) {
  if (!corpus) return null;

  const excluded = Object.entries(corpus.excluded);
  const thin = corpus.beatmaps.played - corpus.beatmaps.reported.length;

  return (
    <div className="flex flex-col gap-xl p-xl">
      {updatedAt && (
        <p className="eyebrow -mb-lg">
          {t("corpus.updatedAt", {
            time: `${String(updatedAt.getHours()).padStart(2, "0")}:${String(
              updatedAt.getMinutes(),
            ).padStart(2, "0")}`,
          })}
        </p>
      )}
      {corpus.insufficient ? (
        <>
          {/* The refusal, where the verdict would have been — same size, same
              place, so "cannot answer yet" is never mistaken for "nothing
              found". */}
          <p className="max-w-[62ch] text-body-lg text-body">{corpus.insufficient}</p>
          {/* The verification survives it. The diagnosis needs ten replays in
              three sessions; the comparison needs five in two a side, so a
              player who has just changed a setting and played a couple of
              evenings under it has a verdict and no diagnosis. Dropping it
              here would hide "put the change back" behind "play more". */}
          {corpus.progress?.verification && (
            <VerificationBlock verification={corpus.progress.verification} t={t} />
          )}
        </>
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

          {!corpus.local_offsets_known && (
            <p className="rounded-sm border border-hairline p-md text-body-sm text-mute">
              {t("corpus.offsetsUnknown")}
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

          {corpus.progress && <ProgressSection progress={corpus.progress} t={t} />}

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
