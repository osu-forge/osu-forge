import { useMemo } from "react";
import { Failure } from "@/components/Parts";
import { PlayCard } from "@/components/Play";
import { Verification } from "@/components/Verification";
import { client } from "@/lib/protocol";
import { failureText, useFeed, useLocale } from "@/lib/session";

/**
 * What has happened since this page was opened.
 *
 * The page to leave on a second monitor while playing: a play lands here the
 * moment the server has finished reading it, and the corpus verdict underneath
 * moves with it. Nothing is polled and nothing reloads — the server pushes both
 * down the socket the page opened when it loaded.
 *
 * It starts empty and it says so, in the same size and place the plays will
 * appear. That is the honest description of what this page is: it witnesses,
 * and a witness that had already seen things before it arrived would be
 * inventing them. Nothing on the server records what a page has been told, so
 * there is nothing to restore, and a list rebuilt from the replay listing would
 * be the same list `/replays/` already shows — under a heading claiming those
 * plays happened just now.
 *
 * The name is the plan's, not the code's. `LiveHud` is the readout drawn over
 * the playfield and belongs to the viewer, and `forge live` is a separate
 * command that writes a standalone file and shares nothing with the server. The
 * only genuinely live thing this server has is the socket, so the socket is
 * what this page is made of.
 */
export function LiveSession({ token }: { token: string }) {
  const api = useMemo(() => client(token), [token]);
  const t = useLocale();
  const { corpus, arrivals, failure } = useFeed(api);

  if (failure) return <Failure text={failureText(failure, t)} />;

  return (
    <div className="flex flex-col gap-xl p-xl">
      <section>
        <h2 className="eyebrow mb-sm">{t("live.heading")}</h2>
        {arrivals.length === 0 ? (
          <p className="max-w-[62ch] text-body-lg text-body">{t("live.waiting")}</p>
        ) : (
          <ul className="m-0 list-none p-0">
            {arrivals.map((entry) => (
              <li key={entry.name}>
                <PlayCard entry={entry} t={t} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* The corpus underneath them, which is the point of watching plays
          arrive rather than reading them one at a time: one play is a play, and
          what the tool has to say is about what several of them keep doing. */}
      {corpus && (
        <section className="flex flex-col gap-lg">
          <h2 className="eyebrow">{t("live.corpus")}</h2>
          <p className="max-w-[62ch] text-body-md text-body">
            {corpus.insufficient ?? corpus.summary}
          </p>
          {corpus.progress?.verification && (
            <Verification verification={corpus.progress.verification} t={t} />
          )}
        </section>
      )}
    </div>
  );
}
