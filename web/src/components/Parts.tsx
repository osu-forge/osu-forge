import type { ReactNode } from "react";
import type { Translator } from "@/i18n";

/**
 * The pieces more than one panel is built out of.
 *
 * Small enough that copying them looked cheaper than sharing them, which is how
 * there came to be two metric tiles with identical bodies and two banners with
 * the same six classes. The cost of the copies is not the lines; it is that
 * "measured" and "assumed" end up looking different on two pages that mean the
 * same thing by them.
 */

/**
 * One number, with what it is and — where it needs one — what it means.
 *
 * The value is the largest text in the tile and the label the smallest, which
 * is the whole argument for a tile: a reader scanning four of them is reading
 * the numbers and only reads a label once one of the numbers surprises them.
 */
export function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="text-display-xs tabular-nums">{value}</div>
      {hint && <div className="mt-xxs text-body-sm text-mute">{hint}</div>}
    </div>
  );
}

/**
 * A standing caveat about everything below it.
 *
 * Boxed rather than coloured. These say what an answer does not cover — that
 * local offsets are unknown, that nothing here is a recommendation yet — and
 * painting them as warnings would rank them against each other when they are
 * all simply true.
 */
export function Banner({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-sm border border-hairline p-md text-body-sm text-mute">{children}</p>
  );
}

/**
 * That nothing here is advice yet, and each separate reason it is not.
 *
 * The reasons arrive as a list because they are one: the header screen writes
 * its own and the differential oracle writes its own, and each is a whole
 * sentence with its own full stops inside it and no capital at the front. Run
 * together with a space they read as a single broken sentence — a full stop
 * from the middle of the first reason, then the lowercase start of the second —
 * so a reader cannot tell one blocker from two. Each keeps its own line, which
 * is what the corpus panel already does with an axis's evidence beside it.
 *
 * Not translated, deliberately: these come from the Python side as generated
 * prose, like every other sentence the server writes about its own findings.
 */
export function MeasurementOnly({
  blockers,
  t,
}: {
  blockers: readonly string[];
  t: Translator;
}) {
  return (
    <Banner>
      <span className="text-ink">{t("corpus.measurementOnly")}</span>
      {blockers.map((reason) => (
        // A block `span` rather than a paragraph: the banner is itself a `<p>`,
        // and a browser meeting a `<p>` inside one closes the outer element
        // early — which takes the border away from everything after it.
        <span key={reason} className="mt-xs block">
          {reason}
        </span>
      ))}
    </Banner>
  );
}

/**
 * The page could not talk to the server, said in the page's own place.
 *
 * It replaces the panel and not the navigation. When the token has been
 * rejected the fix is to restart `forge serve`, and a page that also took the
 * links away would leave nowhere to go and no way to tell whether the other
 * pages are answering.
 */
export function Failure({ text }: { text: string }) {
  return (
    <div className="p-4xl">
      <p className="text-body-lg text-body">{text}</p>
    </div>
  );
}
