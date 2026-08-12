import type { CorpusVerification } from "@/lib/protocol";
import type { Translator } from "@/i18n";
import { signed } from "@/lib/format";

/**
 * Whether the change did what it predicted — the only measurement in this tool
 * that can come out wrong, and the reason the rest of it is not self-confirming.
 *
 * Mounted from four places now, which is why it is a module rather than a
 * private piece of the corpus panel: beside the shift when the corpus can be
 * diagnosed, beside the corpus refusal when it cannot, on the overview where it
 * is the one line worth reading first, and under a live session where it is the
 * number moving. It carries its own heading and no outer spacing, so whichever
 * caller mounts it decides where it sits.
 *
 * No interval here, deliberately. The shift the corpus panel draws above it
 * already puts one on this same difference, and the two are not the same
 * number: where the bootstrap route wins they are drawn independently and
 * disagree in the second decimal. Two lines in one section, both labelled 95%
 * CI and disagreeing about one difference, is worse than one line. The payload
 * still carries `ci_low`/`ci_high` for a reader that wants the whole record.
 */

const VERDICT_COLOUR: Record<string, string> = {
  contradicted: "text-[color:var(--color-judge-miss)]",
  confirmed: "text-[color:var(--color-accent-breeze)]",
};

export function Verification({
  verification,
  t,
}: {
  verification: CorpusVerification;
  t: Translator;
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
