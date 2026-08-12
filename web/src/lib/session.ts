import { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_LOCALE,
  detectLocale,
  translator,
  type Locale,
  type Translator,
} from "@/i18n";
import { ProtocolError, type Client, type Corpus, type Entry } from "@/lib/protocol";

/**
 * What each page of the dashboard needs from the server, and needs identically.
 *
 * "Shared" here means built the same way on every page rather than kept between
 * them. These are separate documents with no router and no shared JavaScript
 * context: a navigation tears down the island, the socket and everything either
 * of them was holding. That is the price of real pages, and it is paid
 * knowingly — what a reviewer needs while reviewing all lives on `/replays/`,
 * which is one document they never have to leave.
 *
 * So each of these is a fresh start rather than a cache. What they exist to
 * prevent is the same fetch being written four slightly different ways, where
 * one page treats a 404 corpus as an error and another treats a rejected token
 * as an empty one.
 */

/**
 * The dictionary for whoever is reading, once the browser has been asked.
 *
 * English for one frame, then the detected locale — the detection needs
 * `navigator`, which is not there while the island is being prerendered into
 * the HTML at build time. Per document, so the flash happens once per
 * navigation. There is nothing to persist: the page offers no override, so the
 * answer is the same on every page and carrying it between them would only make
 * one page's guess outlive the browser setting it came from.
 */
export function useLocale(): Translator {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);
  useEffect(() => {
    setLocale(detectLocale(navigator.languages ?? [navigator.language]));
  }, []);
  return useMemo(() => translator(locale), [locale]);
}

export interface Feed {
  /** The corpus answer, or `null` when this server has none. */
  corpus: Corpus | null;
  /** When that answer arrived, so a page left open shows whether it is looking
   *  at something from just now or from before lunch. */
  corpusAt: Date | null;
  /** Whether the server has been asked yet. `corpus` is `null` both before the
   *  question and after a "there is none", and those are a spinner and a
   *  sentence — opposite things to put on a page. */
  asked: boolean;
  /** Plays that have finished since this document was opened, newest first.
   *  Never restored: nothing on the server remembers what a page has already
   *  been told, so a page that filled this in from somewhere would be inventing
   *  the one thing it is here to witness. */
  arrivals: Entry[];
  /** Why the server could not be reached, if it could not. A 404 corpus is not
   *  one of these — that is an answer, and it arrives as `corpus: null`. */
  failure: unknown;
}

/**
 * The corpus, and the plays that arrive while the page is open.
 *
 * One socket per document, opened here so that every page that wants either of
 * these opens exactly one rather than one per hook. The server pushes a fresh
 * corpus after each new play, so a page left on a second monitor stays right
 * without polling and without reloading — which is the whole reason the socket
 * exists, since a page that reloads itself loses the scroll position and the
 * selection every time it does.
 */
export function useFeed(api: Client): Feed {
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [corpusAt, setCorpusAt] = useState<Date | null>(null);
  const [asked, setAsked] = useState(false);
  const [arrivals, setArrivals] = useState<Entry[]>([]);
  const [failure, setFailure] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .corpus()
      .then((found) => {
        if (cancelled) return;
        setCorpus(found);
        if (found) setCorpusAt(new Date());
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(error);
      })
      .finally(() => {
        if (!cancelled) setAsked(true);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  useEffect(
    () =>
      api.watch(
        (entry) =>
          setArrivals((current) =>
            // The server broadcasts to every socket, and a page that was open
            // through a reconnect can be told twice about one play.
            current.some((item) => item.name === entry.name) ? current : [entry, ...current],
          ),
        (fresh) => {
          setCorpus(fresh);
          setCorpusAt(new Date());
        },
      ),
    [api],
  );

  return { corpus, corpusAt, asked, arrivals, failure };
}

/**
 * What to put on the page instead of the page.
 *
 * Two sentences, because there are two things to do about it: a rejected token
 * means this HTML outlived the run of `forge serve` that stamped it, and
 * restarting is the fix. Anything else is the server not answering at all.
 */
export function failureText(error: unknown, t: Translator): string {
  return error instanceof ProtocolError && error.message === "unauthorised"
    ? t("error.unauthorised")
    : t("error.unreachable");
}
