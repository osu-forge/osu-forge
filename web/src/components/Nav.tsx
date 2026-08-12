import { useLocale } from "@/lib/session";
import type { Key } from "@/i18n";

/**
 * The four places this tool has to say something from.
 *
 * Real links to real documents, not a view switch: each one is a page the
 * server serves, so a browser's back button, a bookmark and an OBS scene
 * pointed at one of them all work without this file knowing about any of them.
 *
 * Every `href` is absolute, and that is load-bearing rather than tidy. A page
 * built as `corpus/index.html` is answered at both `/corpus` and `/corpus/`,
 * and a relative `href="replays/"` written here would resolve against `/` from
 * one of those and against `/corpus/` from the other — so the same link would
 * reach a different page depending on whether the browser that followed the
 * previous one kept the slash. `core/tests/test_server_assets.py` asserts this
 * over the built output, because it is not a mistake that shows up in
 * development.
 *
 * An island of its own rather than markup in the layout, because the labels are
 * translated and the locale is only knowable in a browser. It renders in
 * English into the HTML at build time and re-renders once hydration has asked
 * `navigator` — the same one-frame flash the rest of the page has, and the
 * reason there is no fifth entry here for a language switch: there is nothing
 * to switch, the browser has already said.
 */

interface Route {
  href: string;
  label: Key;
}

const ROUTES: readonly Route[] = [
  { href: "/", label: "nav.overview" },
  { href: "/replays/", label: "nav.replays" },
  { href: "/corpus/", label: "nav.corpus" },
  { href: "/live/", label: "nav.live" },
];

export function Nav({ current }: { current: string }) {
  const t = useLocale();

  return (
    <nav className="hairline-b flex flex-wrap items-baseline gap-lg px-xl py-md">
      <h1 className="text-display-xs">{t("app.title")}</h1>
      <ul className="m-0 flex list-none flex-wrap items-baseline gap-lg p-0">
        {ROUTES.map((route) => (
          <li key={route.href}>
            <a
              href={route.href}
              // `page` rather than `true`: this is the document being read, and
              // the value is what tells a screen reader that rather than just
              // that something here is current.
              aria-current={route.href === current ? "page" : undefined}
              className="text-body-sm text-mute aria-[current=page]:text-ink hover:text-ink-hover"
            >
              {t(route.label)}
            </a>
          </li>
        ))}
      </ul>
      {/* The one claim this tool makes about itself, on every page it makes it
          from. Hidden on a narrow window rather than wrapped: it is a standing
          statement, not a heading, and it must not push the links off the top
          of a phone. */}
      <span className="eyebrow ml-auto hidden sm:block">{t("app.advisory")}</span>
    </nav>
  );
}
