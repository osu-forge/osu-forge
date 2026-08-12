import { useEffect, useMemo, useState } from "react";
import { Banner, Failure, MeasurementOnly, Metric } from "@/components/Parts";
import { PlayCard } from "@/components/Play";
import { Verification } from "@/components/Verification";
import { hourMinute } from "@/lib/format";
import { client, newestPlay, type Entry } from "@/lib/protocol";
import { failureText, useFeed, useLocale } from "@/lib/session";

/**
 * Is anything wrong, and what happened last.
 *
 * The two questions someone opening this tool actually has, answered without
 * loading a replay — which is what the page that used to be here could not do,
 * because it was the replay viewer wearing the front door's URL.
 *
 * Everything on it is a reading the server already holds, and nothing on it is
 * computed here. That is the rule that keeps a dashboard honest: a summary that
 * derives its own numbers is a second implementation of the analysis, and the
 * two disagree eventually. Where the server has no answer this page says the
 * server's own sentence about why, in the place the answer would have been —
 * "not enough data" and "nothing wrong" are opposite conclusions and they look
 * identical as an empty box.
 *
 * The heavy fetch is deliberate and it is the one cost of the split. The
 * listing endpoint sends every replay's whole header, hit objects and
 * judgements included, and this page reads six fields out of the newest one.
 * There is no lighter way to learn which play is the newest, so the page pays
 * it; a summary endpoint on the server is the fix, and it is not this change's
 * to make.
 */
export function Overview({ token }: { token: string }) {
  const api = useMemo(() => client(token), [token]);
  const t = useLocale();
  const feed = useFeed(api);

  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  // The two addresses the single-page version answered. An OBS scene can be
  // pointed at `#corpus` or at `#r=<replay>` and left there for months, and the
  // whole point of putting the view in the address was that it would keep
  // working — so the fragments are honoured once, here, by sending them to the
  // pages that took them over. `replace` rather than assignment: the address
  // they came from is not somewhere the back button should return to.
  useEffect(() => {
    const hash = location.hash;
    if (hash === "#corpus") location.replace("/corpus/");
    else if (hash.startsWith("#r=")) location.replace(`/replays/${hash}`);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .list()
      .then((found) => {
        if (!cancelled) setEntries(found);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(error);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (failure || feed.failure) {
    return <Failure text={failureText(failure ?? feed.failure, t)} />;
  }

  const corpus = feed.corpus;
  // Decided by when each play was played, never by where it sits in the
  // listing. `/api/replays` answers in the order the server's own table was
  // filled, which is newest-first for the startup scan and then appends every
  // play that finishes while it runs — so its first entry stops being the
  // newest as soon as the second play of the evening lands, and this page would
  // go on naming a play from before the session started. What the plays arrived
  // in is a second such order and no more reliable, so the two lists are simply
  // pooled and the headers are asked. A play with no analysis carries no time
  // and cannot be ranked; `known[0]` is what is left when none of them can be,
  // and it is the arrivals first, since those did at least happen while this
  // page was watching.
  const known = feed.arrivals.concat(entries ?? []);
  const newest = newestPlay(known) ?? known[0] ?? null;
  const excluded = corpus ? Object.keys(corpus.excluded).length : 0;

  return (
    <div className="flex flex-col gap-xl p-xl">
      <section className="flex flex-col gap-lg">
        <div className="flex flex-wrap items-baseline justify-between gap-lg">
          <h2 className="eyebrow">{t("corpus.title")}</h2>
          {feed.corpusAt && (
            <p className="eyebrow">{t("corpus.updatedAt", { time: hourMinute(feed.corpusAt) })}</p>
          )}
        </div>
        {/* The corpus's own sentence, or its own refusal, or that there is no
            corpus at all — one line, one place, whichever of the three it is,
            so the shape of this page never says anything the server did not. */}
        <p className="max-w-[62ch] text-body-lg text-body">
          {corpus
            ? (corpus.insufficient ?? corpus.summary)
            : feed.asked
              ? t("overview.noCorpus")
              : t("replays.loading")}
        </p>

        {corpus && (
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

            {/* The standing caveats, kept together and kept above the readings
                they qualify. Each one is a thing this answer does not cover,
                and reading a verdict before them is reading it as though it
                covered them. */}
            {!corpus.health.may_recommend && (
              <MeasurementOnly blockers={corpus.health.blockers} t={t} />
            )}
            {!corpus.local_offsets_known && <Banner>{t("corpus.offsetsUnknown")}</Banner>}
            {excluded > 0 && (
              <Banner>
                <a href="/corpus/" className="hover:text-ink-hover">
                  {t("corpus.excluded", { n: excluded })}
                </a>
              </Banner>
            )}
          </>
        )}
      </section>

      {/* Whether the last change did what it predicted. Above the axes because
          it is the one number on this page that could have come out wrong, and
          therefore the one worth reading first. */}
      {corpus?.progress?.verification && (
        <Verification verification={corpus.progress.verification} t={t} />
      )}

      {corpus && corpus.axes.length > 0 && (
        <section>
          <h2 className="eyebrow mb-sm">{t("corpus.axes")}</h2>
          <ul className="m-0 list-none p-0">
            {corpus.axes.map((axis) => (
              <li
                key={axis.name}
                className="hairline-b flex items-baseline justify-between gap-lg py-md last:border-0"
              >
                <span className="text-body-md text-ink">{axis.verdict}</span>
                {/* The verdict and whether anything can be done about it, and
                    nothing else. The evidence under each axis is the corpus
                    page's job; carried here it would make a summary that is
                    longer than the thing it summarises. */}
                <span
                  className={`shrink-0 rounded-pill border border-hairline px-sm text-body-sm ${
                    axis.actionable ? "text-[color:var(--color-accent-breeze)]" : "text-mute"
                  }`}
                >
                  {axis.actionable ? t("corpus.actionable") : t("corpus.notActionable")}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-md">
            <a
              href="/corpus/"
              className="text-body-sm text-[color:var(--color-accent-breeze)] hover:text-ink-hover"
            >
              {t("overview.corpusLink")} →
            </a>
          </p>
        </section>
      )}

      <section>
        <h2 className="eyebrow mb-sm">{t("overview.lastPlay")}</h2>
        {entries === null && feed.arrivals.length === 0 ? (
          <p className="text-body-sm text-mute">{t("replays.loading")}</p>
        ) : newest ? (
          <ul className="m-0 list-none p-0">
            <li>
              <PlayCard entry={newest} t={t} />
            </li>
          </ul>
        ) : (
          <p className="text-body-sm text-mute">{t("replays.empty")}</p>
        )}
        {entries !== null && entries.length > 0 && (
          <p className="mt-md flex flex-wrap items-baseline gap-lg">
            <a
              href="/replays/"
              className="text-body-sm text-[color:var(--color-accent-breeze)] hover:text-ink-hover"
            >
              {t("overview.viewerLink")} →
            </a>
            <span className="eyebrow">{t("replays.count", { n: entries.length })}</span>
          </p>
        )}
      </section>
    </div>
  );
}
