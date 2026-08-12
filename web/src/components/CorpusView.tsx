import { useMemo } from "react";
import { CorpusPanel } from "@/components/Corpus";
import { Failure } from "@/components/Parts";
import { client } from "@/lib/protocol";
import { failureText, useFeed, useLocale } from "@/lib/session";

/**
 * The corpus panel, given a page of its own.
 *
 * All this does is fetch. The panel below it was already written to be handed
 * an answer and a time, and the only thing it could not say was what to do when
 * there is no answer at all — it rendered nothing, which was right while it was
 * a section of another page and is wrong now that it is the whole of one. A
 * page that answers a navigation with a blank screen is indistinguishable from
 * a page that broke.
 */
export function CorpusView({ token }: { token: string }) {
  const api = useMemo(() => client(token), [token]);
  const t = useLocale();
  const { corpus, corpusAt, asked, failure } = useFeed(api);

  if (failure) return <Failure text={failureText(failure, t)} />;
  if (!corpus) {
    return (
      <div className="p-xl">
        <p className="max-w-[62ch] text-body-lg text-body">
          {asked ? t("overview.noCorpus") : t("replays.loading")}
        </p>
      </div>
    );
  }
  return <CorpusPanel corpus={corpus} updatedAt={corpusAt} t={t} />;
}
