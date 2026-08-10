import type { Analysis, Break, Finding, ReplayHeader } from "@/lib/protocol";
import { causeOf } from "@/lib/cause";
import { ErrorHistogram } from "@/components/ErrorHistogram";
import type { Key } from "@/i18n";

/**
 * What the play cost, and where.
 *
 * Three rules hold this together, and each one is a place a nicer-looking
 * panel would quietly lie:
 *
 * **A share is meaningless without the share it is compared to.** A group
 * holding 8% of the objects and 42% of the loss is doing five times its share
 * of the damage; one holding 8% of both is doing exactly its share and says
 * nothing at all. So both numbers appear together, and the ratio is the
 * headline rather than the loss on its own.
 *
 * **A number the simulation could not reproduce is labelled.** A play whose
 * judgement counts do not match the game's own still has an unstable rate, and
 * that rate describes something other than what happened.
 *
 * **A withheld number says why.** Slider-break detection is known wrong against
 * the header's max combo, so the count is hidden with its reason attached
 * rather than shown as though it were sound.
 */

export interface AnalysisPanelProps {
  analysis: Analysis | null;
  /** Needed for the map's own judgement windows: "40 ms late" means one thing
   *  at OD 4 and another at OD 9, so a cause is read against them. */
  header: ReplayHeader;
  t: (key: Key, values?: Record<string, string | number>) => string;
  onSeek?: (time: number) => void;
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="text-display-xs tabular-nums">{value}</div>
      {hint && <div className="mt-xxs text-body-sm text-mute">{hint}</div>}
    </div>
  );
}

function ratio(finding: Finding): number {
  return finding.share_of_objects > 0 ? finding.share_of_loss / finding.share_of_objects : 0;
}

function FindingRow({ finding }: { finding: Finding }) {
  const multiple = ratio(finding);
  // Only a group carrying more than its share is worth reading as a problem.
  // Below one it is doing less damage than its size predicts, which is the
  // opposite of a finding and is shown neutrally.
  const notable = multiple >= 1.5;
  return (
    <li className="hairline-b py-md last:border-0">
      <div className="flex items-baseline justify-between gap-lg">
        <span className="text-body-md">{finding.label}</span>
        <span
          className={`tabular-nums text-body-sm ${
            notable ? "text-[color:var(--color-accent-sunset)]" : "text-mute"
          }`}
        >
          ×{multiple.toFixed(2)}
        </span>
      </div>
      <div className="eyebrow mt-xxs normal-case tracking-normal">
        {(finding.share_of_objects * 100).toFixed(1)}% of objects →{" "}
        {(finding.share_of_loss * 100).toFixed(1)}% of the loss · {finding.objects} objects ·{" "}
        {(finding.accuracy * 100).toFixed(2)}% within
      </div>
    </li>
  );
}

function BreakRow({
  item,
  header,
  onSeek,
}: {
  item: Break;
  header: ReplayHeader;
  onSeek?: (time: number) => void;
}) {
  const seconds = Math.max(0, Math.floor(item.time / 1000));
  const stamp = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const cause = causeOf(item, header);
  return (
    <li>
      <button
        type="button"
        onClick={() => onSeek?.(item.time)}
        className="hairline-b block w-full cursor-pointer py-md text-left last:border-0 hover:text-ink-hover"
      >
        <div className="flex items-baseline justify-between gap-lg">
          <span className="font-mono text-body-sm tabular-nums">{stamp}</span>
          <span className="text-body-sm tabular-nums text-mute">
            −{item.combo_lost}x
          </span>
        </div>
        {/* The cause, carrying the number it was read from. A verdict without
            its evidence is a guess wearing a uniform, and the number is
            usually the more useful half. */}
        <div className="mt-xxs max-w-[46ch] text-body-sm text-body">{cause.text}</div>
      </button>
    </li>
  );
}

export function AnalysisPanel({ analysis, header, t, onSeek }: AnalysisPanelProps) {
  if (!analysis) {
    return (
      <div className="p-xl">
        <p className="text-body-sm text-mute">{t("analysis.none")}</p>
      </div>
    );
  }

  const ur = analysis.unstable_rate;
  const mean = analysis.mean_error;

  return (
    <div className="flex flex-col gap-xl p-xl">
      {!analysis.usable && (
        <p className="rounded-sm border border-hairline p-md text-body-sm text-[color:var(--color-accent-sunset)]">
          {t("analysis.unreproduced")} {analysis.agreement_reason}
        </p>
      )}

      <div className="grid grid-cols-2 gap-lg sm:grid-cols-4">
        <Metric
          label={t("analysis.accuracy")}
          value={`${(analysis.accuracy * 100).toFixed(2)}%`}
        />
        <Metric
          label={t("analysis.unstableRate")}
          value={ur === null ? "—" : ur.toFixed(1)}
          hint={t("analysis.spreadHint")}
        />
        <Metric
          label={t("analysis.meanError")}
          value={mean === null ? "—" : `${mean >= 0 ? "+" : ""}${mean.toFixed(2)} ms`}
          hint={t("analysis.biasHint")}
        />
        <Metric
          label={t("analysis.combo")}
          value={`${analysis.max_combo}x`}
          hint={t("analysis.comboReported", { n: analysis.reported_max_combo })}
        />
      </div>

      {analysis.combo_caveat && (
        <p className="text-body-sm text-mute">{analysis.combo_caveat}</p>
      )}

      {analysis.errors.length >= 5 && (
        <section>
          <h2 className="eyebrow mb-sm">{t("analysis.histogram")}</h2>
          <ErrorHistogram analysis={analysis} header={header} t={t} />
        </section>
      )}

      {analysis.findings.length > 0 && (
        <section>
          <h2 className="eyebrow mb-sm">{t("analysis.whereLost")}</h2>
          <p className="mb-md max-w-[70ch] text-body-sm text-mute">
            {t("analysis.whereLostHint")}
          </p>
          <ul className="m-0 list-none p-0">
            {analysis.findings.map((finding) => (
              <FindingRow key={finding.feature + finding.label} finding={finding} />
            ))}
          </ul>
        </section>
      )}

      {analysis.breaks.length > 0 && (
        <section>
          <h2 className="eyebrow mb-sm">{t("analysis.breaks")}</h2>
          <ul className="m-0 list-none p-0">
            {analysis.breaks.map((item) => (
              <BreakRow
                key={`${item.time}-${item.kind}`}
                item={item}
                header={header}
                onSeek={onSeek}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
