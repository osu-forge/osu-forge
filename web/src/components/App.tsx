import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnalysisPanel } from "@/components/Analysis";
import { CorpusPanel } from "@/components/Corpus";
import { Playfield } from "@/components/Playfield";
import { ErrorTimeline } from "@/components/ErrorTimeline";
import { DEFAULT_LOCALE, detectLocale, translator, type Locale } from "@/i18n";
import {
  client,
  ProtocolError,
  type Corpus,
  type Entry,
  type ReplayHeader,
  type Samples,
} from "@/lib/protocol";

/**
 * The one island on the page.
 *
 * The clock is held in a ref and mirrored into state once per animation frame,
 * rather than being state that every play tick writes. Sixty state updates a
 * second through React would re-render the replay list and the header for a
 * number only two canvases read.
 */

interface Loaded {
  header: ReplayHeader;
  samples: Samples;
  paths: Float32Array;
}

/** The game's own accuracy formula, from the counts every header carries. */
function accuracyOf(counts: ReplayHeader["counts"]): number {
  const total = counts["300"] + counts["100"] + counts["50"] + counts.miss;
  if (total === 0) return 0;
  return (300 * counts["300"] + 100 * counts["100"] + 50 * counts["50"]) / (300 * total);
}

/** `MM-DD` from the analysis timestamp, or nothing when the play has none. */
function playedDay(entry: Entry): string | null {
  const at = entry.header.analysis?.played_at;
  return at ? at.slice(5, 10) : null;
}

export function App({ token }: { token: string }) {
  const api = useMemo(() => client(token), [token]);
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);
  const t = useMemo(() => translator(locale), [locale]);

  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  /** The most recent play that arrived while the page was open, marked so it
   *  is findable without stealing the selection from whatever is being
   *  watched. Switching for them would interrupt exactly the review they left
   *  the page open to do. */
  const [arrived, setArrived] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  /** `null` until the server has an answer — the entry point stays hidden
   *  rather than opening onto an empty panel. */
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [corpusAt, setCorpusAt] = useState<Date | null>(null);
  const [view, setView] = useState<"replay" | "corpus">(() =>
    typeof location !== "undefined" && location.hash === "#corpus" ? "corpus" : "replay",
  );
  const [query, setQuery] = useState("");

  const [playing, setPlaying] = useState(false);
  const [clock, setClock] = useState(0);
  const clockRef = useRef(0);
  const lastFrame = useRef<number | null>(null);

  useEffect(() => {
    setLocale(detectLocale(navigator.languages ?? [navigator.language]));
  }, []);

  useEffect(() => {
    api
      .list()
      .then((found) => {
        setEntries(found);
        // A deep link names the play to open on; without one, the newest.
        const wanted = decodeURIComponent(location.hash.replace(/^#r=/, ""));
        const linked = location.hash.startsWith("#r=")
          ? found.find((entry) => entry.name === wanted)
          : undefined;
        if (linked) setSelected(linked.name);
        else if (found.length > 0) setSelected(found[0]!.name);
      })
      .catch((error: unknown) => {
        setFailure(
          error instanceof ProtocolError && error.message === "unauthorised"
            ? t("error.unauthorised")
            : t("error.unreachable"),
        );
      });
  }, [api, t]);

  // The address mirrors the view, so a refresh restores it and an OBS scene
  // can point at the corpus (or one replay) and stay there. `replaceState`
  // rather than assignment: browsing plays must not bury the back button.
  useEffect(() => {
    const hash =
      view === "corpus" ? "#corpus" : selected ? `#r=${encodeURIComponent(selected)}` : "";
    history.replaceState(null, "", hash || location.pathname);
  }, [view, selected]);

  // Fetched once; after that the server pushes a fresh answer whenever a new
  // play changes it. Failure leaves the panel absent rather than the page
  // broken — the corpus is an extra reading, not the page's spine.
  useEffect(() => {
    api
      .corpus()
      .then((found) => {
        setCorpus(found);
        if (found) setCorpusAt(new Date());
      })
      .catch(() => setCorpus(null));
  }, [api]);

  // A play that finishes while the page is open is inserted, and nothing else
  // moves. That is the whole difference from a page that reloads itself: the
  // scroll position, the open selection and the playback clock all survive,
  // and those are what made the reloading version unusable.
  useEffect(() => {
    return api.watch(
      (entry) => {
        setEntries((current) => {
          const existing = current ?? [];
          if (existing.some((item) => item.name === entry.name)) return existing;
          return [entry, ...existing];
        });
        setArrived(entry.name);
      },
      (fresh) => {
        setCorpus(fresh);
        setCorpusAt(new Date());
      },
    );
  }, [api]);

  // Space is play/pause anywhere a control does not already own the key —
  // the one shortcut every player's hands expect from a replay page.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space" || view !== "replay" || !loaded) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)) return;
      event.preventDefault();
      setPlaying((on) => !on);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, loaded]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoaded(null);
    api
      .load(selected)
      .then((result) => {
        if (cancelled) return;
        setLoaded(result);
        const start = result.samples.t[0] ?? 0;
        clockRef.current = start;
        setClock(start);
        setPlaying(false);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [api, selected]);

  const seek = useCallback((time: number) => {
    clockRef.current = time;
    setClock(time);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    let frame = 0;
    const end = loaded.samples.t[loaded.samples.t.length - 1] ?? 0;

    const step = (now: number) => {
      if (playing) {
        const previous = lastFrame.current;
        // Real elapsed time, not a fixed increment: a dropped frame must
        // advance the clock by what it cost rather than by one tick, or
        // playback drifts away from the timeline it is drawn against.
        if (previous !== null) clockRef.current += now - previous;
        lastFrame.current = now;
        if (clockRef.current >= end) {
          clockRef.current = end;
          setPlaying(false);
        }
        setClock(clockRef.current);
      } else {
        lastFrame.current = null;
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [loaded, playing]);

  if (failure) {
    return (
      <div className="p-4xl">
        <p className="text-body-lg text-body">{failure}</p>
      </div>
    );
  }

  const header = loaded?.header;
  const needle = query.trim().toLowerCase();
  const shown = (entries ?? []).filter((entry) => {
    if (!needle) return true;
    const beatmap = entry.header.beatmap;
    return `${beatmap.artist} ${beatmap.title} ${beatmap.version}`.toLowerCase().includes(needle);
  });

  return (
    <div className="grid h-screen grid-cols-1 grid-rows-[auto_1fr] md:grid-cols-[300px_1fr] md:grid-rows-1">
      <nav className="hairline-b max-h-[38vh] overflow-y-auto md:hairline-r md:max-h-none md:border-b-0">
        {corpus && (
          <button
            type="button"
            onClick={() => setView("corpus")}
            aria-current={view === "corpus"}
            className="hairline-b block w-full cursor-pointer px-lg py-md text-left aria-[current=true]:bg-canvas-card hover:bg-canvas-card"
          >
            <div className="eyebrow">{t("corpus.title")}</div>
            <div className="mt-xxs text-body-sm text-body">
              {corpus.insufficient
                ? t("corpus.collecting")
                : t("corpus.subtitle", { n: corpus.replays, s: corpus.sessions })}
            </div>
          </button>
        )}
        <div className="hairline-b px-lg py-md">
          <div className="eyebrow">{t("nav.replays")}</div>
          <div className="text-body-sm text-body">
            {entries === null
              ? t("replays.loading")
              : t("replays.count", { n: entries.length })}
          </div>
          {entries !== null && entries.length > 5 && (
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("replays.filter")}
              aria-label={t("replays.filter")}
              className="mt-sm w-full rounded-sm border border-hairline bg-transparent px-sm py-xs text-body-sm text-ink outline-none placeholder:text-mute focus:border-canvas-mid"
            />
          )}
        </div>
        {shown.map((entry) => (
          <button
            key={entry.name}
            type="button"
            onClick={() => {
              setSelected(entry.name);
              setView("replay");
            }}
            aria-current={view === "replay" && entry.name === selected}
            className="hairline-b block w-full cursor-pointer px-lg py-md text-left text-body-sm aria-[current=true]:bg-canvas-card hover:bg-canvas-card"
          >
            <div className="truncate text-ink">
              {entry.header.beatmap.artist} — {entry.header.beatmap.title}
            </div>
            <div className="eyebrow mt-xxs flex items-center gap-sm truncate">
              <span className="truncate">
                [{entry.header.beatmap.version}]
              </span>
              <span className="shrink-0 tabular-nums">
                {(accuracyOf(entry.header.counts) * 100).toFixed(2)}%
              </span>
              {playedDay(entry) && (
                <span className="shrink-0 tabular-nums">{playedDay(entry)}</span>
              )}
              {entry.name === arrived && entry.name !== selected && (
                <span className="shrink-0 rounded-pill border border-hairline px-sm text-[color:var(--color-accent-breeze)]">
                  {t("replays.new")}
                </span>
              )}
            </div>
          </button>
        ))}
        {entries !== null && entries.length > 0 && shown.length === 0 && (
          <p className="px-lg py-md text-body-sm text-mute">{t("replays.noMatch")}</p>
        )}
        {entries?.length === 0 && (
          <p className="px-lg py-md text-body-sm text-mute">{t("replays.empty")}</p>
        )}
      </nav>

      <section className="flex min-h-0 min-w-0 flex-col overflow-y-auto md:overflow-y-visible">
        <header className="hairline-b flex items-baseline gap-lg px-xl py-md">
          <h1 className="text-display-xs">{t("app.title")}</h1>
          {view === "corpus" ? (
            <span className="truncate text-body-sm text-body">{t("corpus.axes")}</span>
          ) : (
            header && (
              <span className="truncate text-body-sm text-body">
                {header.beatmap.artist} — {header.beatmap.title} [{header.beatmap.version}]
              </span>
            )
          )}
          <span className="eyebrow ml-auto">{t("app.advisory")}</span>
        </header>

        {view === "corpus" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <CorpusPanel corpus={corpus} updatedAt={corpusAt} t={t} />
          </div>
        ) : (
          <>
            <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_380px]">
              <div className="min-h-0">
                {loaded && (
                  <Playfield
                    header={loaded.header}
                    samples={loaded.samples}
                    paths={loaded.paths}
                    clock={clock}
                  />
                )}
              </div>
              <aside className="hairline-l min-h-0 overflow-y-auto border-l border-hairline">
                {loaded && (
                  <AnalysisPanel
                    analysis={loaded.header.analysis}
                    header={loaded.header}
                    t={t}
                    onSeek={seek}
                  />
                )}
              </aside>
            </div>

            {loaded && (
              <div className="hairline-t">
                <ErrorTimeline header={loaded.header} clock={clock} onSeek={seek} t={t} />
                <div className="flex items-center gap-lg px-lg py-md">
                  <button
                    type="button"
                    onClick={() => setPlaying((on) => !on)}
                    className="cursor-pointer rounded-pill border border-hairline px-lg py-xs text-body-sm text-ink hover:text-ink-hover"
                  >
                    {playing ? t("player.pause") : t("player.play")}
                  </button>
                  <p className="eyebrow max-w-[62ch] normal-case tracking-normal">
                    {t("player.samplesOnlyHelp")}
                  </p>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
