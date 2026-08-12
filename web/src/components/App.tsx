import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnalysisPanel } from "@/components/Analysis";
import { CorpusPanel } from "@/components/Corpus";
import { KeyOverlay } from "@/components/KeyOverlay";
import { KeysPanel } from "@/components/KeysPanel";
import { LiveHud } from "@/components/LiveHud";
import { Playfield } from "@/components/Playfield";
import { ErrorTimeline } from "@/components/ErrorTimeline";
import { DEFAULT_LOCALE, detectLocale, translator, type Locale } from "@/i18n";
import {
  client,
  MOD_HIDDEN,
  modNames,
  ProtocolError,
  sampleAt,
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
  backdrop: string | null;
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

/** `m:ss` from milliseconds of recording, clamped so a pre-start clock reads 0:00. */
function stamp(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * How far the track may sit from the clock before it is dragged back, in
 * seconds of track time.
 *
 * There has to be a tolerance at all because the two are independent clocks:
 * one is `performance.now`, the other is the sound card's, and matched rates
 * still walk apart by tens of parts per million. There has to be a wide one
 * because assigning `currentTime` restarts decoding — correcting on every
 * animation frame trades a mismatch nobody could hear for a stutter everybody
 * can. An actual seek does not wait for this; `seek` moves the element itself,
 * where the size of the jump is known rather than inferred.
 *
 * 50 ms sits between the two failures. Below it are the things that are not
 * drift: the element reports its position to a few milliseconds, and the two
 * clocks are read up to one animation frame apart — 17 ms at 60 Hz — so
 * ordinary jitter never triggers a correction. Above it is where sound heard
 * against a picture starts to be noticed at all; ITU-R BT.1359-1 puts the
 * detection threshold for audio ahead of video near 45 ms.
 *
 * It is a tolerance in track time, so at 0.25× the same 50 ms takes four times
 * as long to sit through. Scaling it down there was the alternative and it puts
 * the threshold inside the read noise, which buys a correction every few frames
 * to fix an offset that only exists while reviewing in slow motion.
 */
const DRIFT_TOLERANCE = 0.05;

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
  const [speed, setSpeed] = useState(1);
  const [clock, setClock] = useState(0);
  const clockRef = useRef(0);
  const lastFrame = useRef<number | null>(null);
  /** The map's own track as an object URL, once it has arrived. `null` covers a
   *  map that names no audio, a fetch that failed, and a download still in
   *  flight, because the page has one answer for all three: the replay plays
   *  silently. Silence is the fallback — a viewer that broke because a song did
   *  not turn up would be a worse tool than one with no sound at all. */
  const [song, setSong] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  /** Whether the browser has refused to start the track. A refusal is about the
   *  page not having been interacted with yet rather than about this instant,
   *  so asking again on the next animation frame gets the same answer — and a
   *  browser that logs a warning per attempt would log sixty a second. It is
   *  asked again after the next pause, which is a press away. */
  const refused = useRef(false);
  /** Recording bounds of the loaded replay, for clamping every seek. */
  const bounds = useRef<[number, number]>([0, 0]);
  /** A–B section repeat, map milliseconds. Read by the clock loop via a ref
   *  so setting it does not restart the animation frame. */
  const [loop, setLoop] = useState<[number, number] | null>(null);
  const loopRef = useRef(loop);
  useEffect(() => {
    loopRef.current = loop;
  }, [loop]);
  /** Analysis override for Hidden: the faithful render hides what the player
   *  could not see, and this reveals it without pretending the play was made
   *  that way — the HD badge stays up either way. */
  const [revealHidden, setRevealHidden] = useState(false);

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

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    let backdrop: string | null = null;
    setLoaded(null);
    setLoop(null);
    setRevealHidden(false);
    api
      .load(selected)
      .then((result) => {
        if (cancelled) {
          if (result.backdrop) URL.revokeObjectURL(result.backdrop);
          return;
        }
        backdrop = result.backdrop;
        setLoaded(result);
        const start = result.samples.t[0] ?? 0;
        bounds.current = [start, result.samples.t[result.samples.t.length - 1] ?? start];
        clockRef.current = start;
        setClock(start);
        setPlaying(false);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(String(error));
      });
    return () => {
      cancelled = true;
      // The object URL holds the decoded image alive; a replay switch would
      // otherwise leak one background per play browsed.
      if (backdrop) URL.revokeObjectURL(backdrop);
    };
  }, [api, selected]);

  const seek = useCallback((time: number) => {
    const [start, end] = bounds.current;
    const clamped = Math.min(end, Math.max(start, time));
    clockRef.current = clamped;
    setClock(clamped);
    // The clock move the track cannot follow on its own. A seek can be a
    // millisecond — stepping one recorded sample is about sixteen — and would
    // sit under the drift tolerance while the picture had plainly moved, so it
    // is written here, where the size of the jump is known, rather than
    // inferred from a clock that only says where it is now. The loop wrap is
    // the other move that does not come through here: it jumps by a whole
    // section, which the drift branch then catches on the next frame because
    // the delta is enormous rather than because anything routed it.
    // A scrub calls this on every pointer move and that is still one write per
    // move: a seek arriving while one is in progress aborts it, so the drag
    // costs the decoder the position it ends on rather than every position it
    // crossed. The frame loop is what must never come through here.
    const element = audio.current;
    if (element) element.currentTime = Math.max(0, clamped / 1000);
  }, []);

  /** Play, pause, or — from the end — rewind and play again. */
  const toggle = useCallback(() => {
    if (!playing && clockRef.current >= bounds.current[1]) seek(bounds.current[0]);
    if (!playing) {
      // Asked for here as well as from the effect below, and this is the only
      // place it can be asked for from inside the gesture. Chrome and Firefox
      // remember that the document has been interacted with at all, so the
      // effect's later attempt is enough for them; WebKit wants the call on the
      // stack of the press itself, and an effect runs after React has flushed.
      // Harmless where it is not needed: a second play() on an element already
      // playing resolves without doing anything.
      audio.current?.play().catch(() => {
        // The effect owns the refusal. Swallowed rather than latched here,
        // because a press that arrives before the track has decoded rejects
        // for a reason that says nothing about whether sound is allowed.
      });
    }
    setPlaying((on) => !on);
  }, [playing, seek]);

  // The track is fetched once the replay is already on screen, not as part of
  // loading it. It is the largest thing the server has and nothing is drawn
  // from it, so a load that waited on it would hold the field blank for the
  // length of a download that is not needed until someone presses play.
  useEffect(() => {
    if (!loaded) return;
    let cancelled = false;
    let url: string | null = null;
    api
      .song(loaded.header)
      .then((found) => {
        if (cancelled) {
          // Arrived after the replay was switched away from, so nothing on the
          // page owns it and nothing ever will.
          if (found) URL.revokeObjectURL(found);
          return;
        }
        url = found;
        setSong(found);
      })
      .catch(() => {
        // The same silence a map with no track gets. There is nothing better
        // this page could say about the difference, and saying it in an error
        // banner would make a missing song look like a broken replay.
        if (!cancelled) setSong(null);
      });
    return () => {
      cancelled = true;
      setSong(null);
      // A blob holding a whole decoded track — several megabytes, and one per
      // replay browsed if it is not let go. The backdrop below is revoked for
      // the same reason; this one costs an order of magnitude more.
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, loaded]);

  // Removal from the document does pause a media element, but only after the
  // user agent has awaited a stable state — so there is a window where the last
  // replay's track is still audible under the next one, and the length of it is
  // not ours to decide. Stopped by hand instead, from an effect of its own
  // because the ref is already detached by the time a passive cleanup that also
  // fetches gets to run.
  useEffect(() => {
    const element = audio.current;
    return () => element?.pause();
  }, [song]);

  // The clock already advances by the mod rate and by the chosen speed, so the
  // element has to move at their product or it is playing a different moment of
  // the same timeline. Pitch is deliberately not preserved: Double Time raises
  // it in the game, and a track shifted back down to concert pitch is not the
  // sound the play was made against.
  useEffect(() => {
    const element = audio.current;
    if (!element || !loaded) return;
    element.playbackRate = speed * (loaded.header.rate || 1);
    element.preservesPitch = false;
  }, [song, speed, loaded]);

  // The element follows the clock, and never the other way round. The clock is
  // what both canvases are drawn from; letting the track drive it instead would
  // put the picture wherever the decoder happened to be, and the decoder stalls.
  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    // Map time is audio time in osu! — a hit object's time is a timestamp into
    // the song — so the position is the clock in seconds and nothing else. The
    // recording starts before the track and can outlast it, though, and there
    // is nothing to play out there. `duration` is NaN until the metadata is
    // read, which is an end not known yet rather than an end already passed.
    const at = clock / 1000;
    const inside = at >= 0 && (Number.isNaN(element.duration) || at <= element.duration);
    if (!playing || !inside) {
      if (!element.paused) element.pause();
      refused.current = false;
      return;
    }
    // Corrected only while the track is meant to be running. The clock goes on
    // advancing when it is not — a refusal that has latched, a decode that
    // failed — and a position written to a paused element is a seek like any
    // other, so leaving this above the branch bought twenty aborted seeks a
    // second for a track nobody was listening to.
    if (Math.abs(element.currentTime - at) > DRIFT_TOLERANCE) {
      element.currentTime = at;
    }
    // `ended` is asked before `paused` because the two are both true there and
    // they want opposite things: play() on an ended element rewinds it to the
    // start first, so the last frames of a play would restart the song under
    // them. The clock is still inside the track — that is what `ended` means
    // here — so there is nothing left to start.
    if (element.paused && !element.ended && !refused.current) {
      // A browser refuses to start audio before the page has been interacted
      // with, and refuses by rejecting this promise. Not a failure to report:
      // the picture plays either way, and the click or the key that asked for
      // playback is itself the interaction that lets the next attempt through.
      // Left unhandled it would only surface as an unhandled rejection.
      element.play().catch((error: unknown) => {
        // Only a refusal latches. play() also rejects with `AbortError` when a
        // pause interrupts one still resolving, which is ordinary here: on a
        // fresh element the promise stays pending until enough has decoded, so
        // a play and a quick pause race every time. Latching on that would
        // silence the whole of the next playback for having tapped twice.
        if (error instanceof DOMException && error.name === "NotAllowedError") {
          refused.current = true;
        }
      });
    }
  }, [clock, playing, song]);

  useEffect(() => {
    if (!loaded) return;
    let frame = 0;
    const end = loaded.samples.t[loaded.samples.t.length - 1] ?? 0;
    // The wire clock is map time, but the play happened in real time: under
    // Double Time 1.5 map milliseconds passed per wall millisecond. Advancing
    // by the rate makes 1× mean "as fast as it was played" — without it a DT
    // replay reviews in slow motion while claiming full speed.
    const rate = loaded.header.rate || 1;

    const step = (now: number) => {
      if (playing) {
        const previous = lastFrame.current;
        // Real elapsed time scaled by the chosen speed, not a fixed
        // increment: a dropped frame must advance the clock by what it cost
        // rather than by one tick, or playback drifts away from the timeline
        // it is drawn against.
        if (previous !== null) clockRef.current += (now - previous) * speed * rate;
        lastFrame.current = now;
        const section = loopRef.current;
        if (section && clockRef.current >= section[1]) {
          clockRef.current = section[0];
        } else if (clockRef.current >= end) {
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
  }, [loaded, playing, speed]);

  // The reviewer's keyboard: space plays, arrows seek, comma and period step
  // one recorded sample — the frame a key went down is usually the frame
  // being looked for, and a mouse cannot land on it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (view !== "replay" || !loaded) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // A focused button answers Space itself, so acting on it here as well
      // would toggle twice and read as nothing having happened. Only that key,
      // though: a focused button is where a click on the field or on Play
      // leaves the focus, and standing every shortcut down there would silently
      // stop the arrows from seeking and the commas from stepping the moment
      // anyone used the mouse — which is most of what reviewing a play is.
      if (tag === "BUTTON" && event.code === "Space") return;

      const samples = loaded.samples;
      const stepSample = (direction: 1 | -1) => {
        const at = sampleAt(samples.t, clockRef.current);
        const next = Math.min(samples.t.length - 1, Math.max(0, at + direction));
        setPlaying(false);
        seek(samples.t[next]!);
      };
      // Seeks are in experienced seconds, so "2 s" means the same thing on a
      // Double Time replay as on a no-mod one.
      const hop = (event.shiftKey ? 10_000 : 2_000) * (loaded.header.rate || 1);

      switch (event.code) {
        case "Space":
          // Held keys repeat, and this is the only case below whose repeat
          // undoes the one before it — a Space leaned on flickers play and
          // pause tens of times a second. The others are held on purpose:
          // arrows scrub, the commas walk the recording sample by sample, and
          // the brackets re-mark a loop edge that the last press decides. So
          // the guard is on the toggle rather than at the top of the handler,
          // where it would cost those their whole reason for being keys.
          if (event.repeat) break;
          toggle();
          break;
        case "ArrowLeft":
          seek(clockRef.current - hop);
          break;
        case "ArrowRight":
          seek(clockRef.current + hop);
          break;
        case "Comma":
          stepSample(-1);
          break;
        case "Period":
          stepSample(1);
          break;
        case "Home":
          setPlaying(false);
          seek(bounds.current[0]);
          break;
        // A section to study, marked from the playhead: `[` opens it here,
        // `]` closes it here, backslash lets go. An end before the start is
        // ignored rather than guessed at.
        case "BracketLeft":
          setLoop((current) => [clockRef.current, current?.[1] ?? bounds.current[1]]);
          break;
        case "BracketRight":
          setLoop((current) => {
            const from = current?.[0] ?? bounds.current[0];
            return clockRef.current > from ? [from, clockRef.current] : current;
          });
          break;
        case "Backslash":
          setLoop(null);
          break;
        default:
          return;
      }
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, loaded, seek, toggle]);

  if (failure) {
    return (
      <div className="p-4xl">
        <p className="text-body-lg text-body">{failure}</p>
      </div>
    );
  }

  const header = loaded?.header;
  const rate = header?.rate || 1;
  const badges = header ? modNames(header.mods) : [];
  const playedHidden = header ? (header.mods & MOD_HIDDEN) !== 0 : false;
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
              {modNames(entry.header.mods).length > 0 && (
                <span className="shrink-0 font-mono">
                  {modNames(entry.header.mods).join(" ")}
                </span>
              )}
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
              <span className="flex min-w-0 items-baseline gap-sm">
                <span className="truncate text-body-sm text-body">
                  {header.beatmap.artist} — {header.beatmap.title} [{header.beatmap.version}]
                </span>
                {badges.length > 0 && (
                  <span className="shrink-0 font-mono text-body-sm text-[color:var(--color-accent-breeze)]">
                    {badges.join(" ")}
                  </span>
                )}
              </span>
            )
          )}
          <span className="eyebrow ml-auto">{t("app.advisory")}</span>
        </header>

        {/* Outside the view switch, because the clock is: crossing to the
            corpus and back leaves the replay running, and a track torn down
            and rebuilt on the way would come back somewhere else. No
            `controls` either — a second transport with its own idea of the
            position is exactly how the sound ends up ahead of the picture.
            Hidden explicitly because Tailwind's preflight gives every `audio`
            element `display: block`, which puts a zero-height flex item in
            this column for something that has nothing to show. Display has no
            bearing on whether it plays. */}
        {song !== null && <audio ref={audio} src={song} muted={muted} className="hidden" />}

        {view === "corpus" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <CorpusPanel corpus={corpus} updatedAt={corpusAt} t={t} />
          </div>
        ) : (
          <>
            <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_380px]">
              <div className="relative min-h-0 overflow-hidden">
                {loaded?.backdrop && (
                  // Dim enough that the field stays the subject; the image is
                  // orientation, not decoration.
                  <img
                    src={loaded.backdrop}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover opacity-15"
                  />
                )}
                {loaded && (
                  <Playfield
                    header={loaded.header}
                    samples={loaded.samples}
                    paths={loaded.paths}
                    clock={clock}
                    hidden={playedHidden && !revealHidden}
                  />
                )}
                {/* The field plays and pauses on a click, the way a video does,
                    and what does it is a transparent button laid over the field
                    rather than a role on the box that holds it. `role="button"`
                    on the container was the shorter way to write the same thing
                    and it makes every descendant presentational: the HUD's
                    labelled accuracy readout and its error bar leave the
                    accessibility tree entirely, still on screen and no longer
                    announced. The same wrapping is what forced a click on the
                    container to carry a list of tags it must not swallow a
                    press for — a list that fails open, since anything it does
                    not name both acts and toggles, and that cannot be given
                    `[role='button']` without naming the container itself. A
                    sibling neither hides the content nor stands between it and
                    the pointer, so both problems are gone rather than guarded.
                    It exists only while a replay does: a tab stop announcing
                    "Play or pause the replay" over an empty field is a control
                    that does nothing, said out loud. */}
                {loaded && (
                  <button
                    type="button"
                    onClick={toggle}
                    aria-label={t("player.fieldToggle")}
                    className="absolute inset-0 cursor-pointer"
                  />
                )}
                {loaded && <LiveHud header={loaded.header} clock={clock} t={t} />}
              </div>
              <aside className="hairline-l min-h-0 overflow-y-auto border-l border-hairline">
                {loaded && (
                  <>
                    <AnalysisPanel
                      analysis={loaded.header.analysis}
                      header={loaded.header}
                      t={t}
                      onSeek={seek}
                    />
                    {/* Below the analysis rather than inside it: the samples
                        are always there, so this panel still has something to
                        say about a play the analysis could not reproduce. */}
                    <KeysPanel
                      samples={loaded.samples}
                      header={loaded.header}
                      t={t}
                      onSeek={seek}
                    />
                  </>
                )}
              </aside>
            </div>

            {loaded && (
              <div className="hairline-t">
                <ErrorTimeline
                  header={loaded.header}
                  clock={clock}
                  onSeek={seek}
                  loop={loop}
                  t={t}
                />
                <div className="flex flex-wrap items-center gap-md px-lg py-md">
                  <button
                    type="button"
                    onClick={toggle}
                    className="cursor-pointer rounded-pill border border-hairline px-lg py-xs text-body-sm text-ink hover:text-ink-hover"
                  >
                    {playing
                      ? t("player.pause")
                      : clock >= bounds.current[1]
                        ? t("player.replay")
                        : t("player.play")}
                  </button>

                  <div
                    className="flex items-center gap-xs"
                    role="group"
                    aria-label={t("player.speed")}
                  >
                    {[0.25, 0.5, 1, 1.5, 2].map((rate) => (
                      <button
                        key={rate}
                        type="button"
                        onClick={() => setSpeed(rate)}
                        aria-pressed={speed === rate}
                        className={`cursor-pointer rounded-pill border px-sm py-xs font-mono text-body-sm tabular-nums ${
                          speed === rate
                            ? "border-[color:var(--color-accent-breeze)] text-[color:var(--color-accent-breeze)]"
                            : "border-hairline text-mute hover:text-ink-hover"
                        }`}
                      >
                        {rate}×
                      </button>
                    ))}
                  </div>

                  <span className="font-mono text-body-sm tabular-nums text-body">
                    {stamp((clock - bounds.current[0]) / rate)} /{" "}
                    {stamp((bounds.current[1] - bounds.current[0]) / rate)}
                  </span>

                  {/* Only while there is something to silence. A map that
                      names no track, and one whose track did not arrive, are
                      both plays with no sound, and a mute offered over them
                      would be a control with nothing behind it. */}
                  {song !== null && (
                    <button
                      type="button"
                      onClick={() => setMuted((on) => !on)}
                      aria-pressed={muted}
                      className={`cursor-pointer rounded-pill border px-sm py-xs font-mono text-body-sm ${
                        muted
                          ? "border-[color:var(--color-accent-breeze)] text-[color:var(--color-accent-breeze)]"
                          : "border-hairline text-mute hover:text-ink-hover"
                      }`}
                    >
                      {t("player.mute")}
                    </button>
                  )}

                  {playedHidden && (
                    <button
                      type="button"
                      onClick={() => setRevealHidden((on) => !on)}
                      aria-pressed={revealHidden}
                      className={`cursor-pointer rounded-pill border px-sm py-xs font-mono text-body-sm ${
                        revealHidden
                          ? "border-[color:var(--color-accent-breeze)] text-[color:var(--color-accent-breeze)]"
                          : "border-hairline text-mute hover:text-ink-hover"
                      }`}
                    >
                      {t("player.revealHidden")}
                    </button>
                  )}

                  {loop && (
                    <button
                      type="button"
                      onClick={() => setLoop(null)}
                      aria-label={t("player.loopClear")}
                      className="cursor-pointer rounded-pill border border-[color:var(--color-accent-breeze)] px-sm py-xs font-mono text-body-sm tabular-nums text-[color:var(--color-accent-breeze)]"
                    >
                      A–B {stamp(loop[0] - bounds.current[0])}–{stamp(loop[1] - bounds.current[0])} ✕
                    </button>
                  )}

                  {(loaded.header.analysis?.breaks.length ?? 0) > 0 && (
                    <div className="flex items-center gap-xs">
                      {([-1, 1] as const).map((direction) => {
                        const breaks = loaded.header.analysis!.breaks;
                        const target =
                          direction === 1
                            ? breaks.find((item) => item.time > clock + 80)
                            : [...breaks].reverse().find((item) => item.time < clock - 1600);
                        return (
                          <button
                            key={direction}
                            type="button"
                            disabled={!target}
                            onClick={() => {
                              if (!target) return;
                              setPlaying(false);
                              // Land shortly before the break — in experienced
                              // time, so the run-up length does not depend on DT.
                              seek(target.time - 1200 * rate);
                            }}
                            className="cursor-pointer rounded-pill border border-hairline px-sm py-xs text-body-sm text-mute hover:text-ink-hover disabled:cursor-default disabled:opacity-40"
                          >
                            {direction === -1 ? `‹ ${t("player.break")}` : `${t("player.break")} ›`}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  <div className="ml-auto">
                    <KeyOverlay samples={loaded.samples} clock={clock} />
                  </div>
                </div>
                <p className="eyebrow max-w-[100ch] px-lg pb-md normal-case tracking-normal">
                  {t("player.shortcuts")} — {t("player.samplesOnlyHelp")}
                </p>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
