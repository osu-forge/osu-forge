"""The built web application, read once.

The site is an Astro build: `web/dist`, produced by `npm run build`. It is not
committed — it is reproducible from the checkout, and a committed `dist/` is a
second copy of the source that drifts from it silently.

# Read into memory at startup, not from disk per request

The same rule that governs :func:`osuforge.server.app.build_app`: a server that
opens files when asked is a server whose reach is decided by whoever asks it.
Path traversal is not something to be filtered here, it is something that has no
mechanism to exist — the only bytes this can serve are the bytes read from one
directory before the socket was opened.

A built site is a few hundred kilobytes. Holding it costs nothing next to the
replay payloads already resident.

# The token substitution

Every page contains the literal `__TOKEN__`, which Astro emits from the page
source and which is replaced when the page is served. It is in the HTML rather
than fetched because a browser navigating to a URL cannot send an
`Authorization` header, and a cross-site page can cause that navigation but
cannot read the response.

Substitution happens per request rather than at load, so the same loaded site
can be served by a run with a different token — and so the token is never
resident in the asset table.

# Every `.html` is a page, and no page is an asset

An asset is served as the bytes that are on disk. A page reaching the browser
that way would still be carrying `__TOKEN__`, so every call it made would
present the literal placeholder and be answered 401: a page that loads, looks
finished, and fails at everything, with nothing on either side saying why. The
split is made on the extension rather than on a list of known page names,
because a list is a thing a new page in `web/src/pages` can be missing from.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SITE_ENV", "Site", "SiteMissingError", "default_site_root", "load_site"]

SITE_ENV = "OSU_FORGE_SITE"
"""Environment variable pointing at a built site, overriding discovery."""

_TOKEN_PLACEHOLDER = "__TOKEN__"

# Only what a static site is made of, pages excepted. An extension absent from
# this map is not served at all rather than served as a guess:
# `application/octet-stream` on a file the browser expected to execute produces
# a blank page and an hour of looking in the wrong place.
#
# `.html` is deliberately not here. An entry for it would be a second, quieter
# route by which a page could be served as bytes, which is the one thing the
# module docstring argues must not be possible.
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class SiteMissingError(RuntimeError):
    """The built site is not there, with the command that builds it."""


@dataclass(frozen=True, slots=True)
class Site:
    """A built site, in memory."""

    pages: dict[str, str] = field(default_factory=dict)
    """URL path to HTML, each still holding the token placeholder.

    Keyed by what a browser will ask for rather than by where the file sits. A
    directory-format build emits `ping/index.html`, and a link written `/ping`
    is followed as `/ping` by one browser and `/ping/` by another, so both are
    keys for the same page. Answering the slashless form with the page instead
    of a redirect to the slashed one costs a round trip less, and is safe only
    because every URL the built page names is absolute — a relative one would
    resolve against `/` from `/ping` and against `/ping/` from `/ping/`.
    """

    assets: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    """URL path to `(bytes, content type)`. Paths begin with `/`.

    Never a page: see the module docstring for what serving one from here would
    do.
    """

    root: Path = Path()
    skipped: dict[str, int] = field(default_factory=dict)
    """Extensions found but not served, by count."""

    script_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """`sha256-…` for the inline `<script>`s of each page, by URL path.

    Astro emits a small inline script to start hydration, and a policy of
    `default-src 'self'` blocks it — correctly, since the policy cannot tell
    that script apart from an injected one. The alternatives are
    `'unsafe-inline'`, which turns the protection off for every script, or
    naming the exact contents. Hashing costs nothing here because the page is
    already in memory, and it keeps the policy meaning what it says.

    Per page, not one set naming every page's hashes. Astro emits a different
    inline script per page, so a union would be the smallest policy that admits
    them all — and it would also admit page A's script inside page B, where it
    was never shipped. That is the hole the hashes were there to close: an
    injection needs an allowed body, and the union hands it several.
    """

    def policy(self, path: str) -> str:
        """The Content-Security-Policy for what is served at `path`.

        Delivered as a header rather than a `<meta>`: `frame-ancestors` is
        ignored in a meta element, which browsers say out loud, and it is the
        directive that stops this page being framed by something else.

        `path` chooses the script hashes and nothing else. A path holding no
        page is answered with a policy naming none, which is the honest
        default: an asset or a 404 has no document for a script to run in, and
        neither should be carrying permission for one.
        """
        scripts = " ".join(f"'{value}'" for value in self.script_hashes.get(path, ()))
        return "; ".join(
            [
                "default-src 'self'",
                f"script-src 'self' {scripts}".rstrip(),
                # Tailwind emits a style element, and hashing those would have
                # to be redone on every rebuild for no gain: a stylesheet
                # cannot exfiltrate anything with connect-src locked down.
                "style-src 'self' 'unsafe-inline'",
                # `blob:` because the beatmap background cannot ride on an
                # `<img src>`: the endpoint wants the session token, so the page
                # fetches it with the header and wraps the response in an object
                # URL. Without this the fetch succeeds and the browser then
                # refuses to paint what it downloaded. It grants nothing extra —
                # only same-origin script can mint a `blob:`, and `script-src`
                # allows none this page did not ship with.
                "img-src 'self' data: blob:",
                # The same argument for the song, which otherwise falls back to
                # `default-src 'self'` and is refused as a `blob:` after it has
                # already been downloaded. Audio failing that way is quieter
                # than an image failing: the play simply has no sound, and
                # nothing on the page says why.
                "media-src 'self' blob:",
                "font-src 'self'",
                "connect-src 'self'",
                "base-uri 'none'",
                "form-action 'none'",
                "frame-ancestors 'none'",
                "object-src 'none'",
            ]
        )

    @property
    def pages_without_token(self) -> tuple[str, ...]:
        """The pages that will not receive a token, by URL.

        A page whose placeholder went missing fetches without one and is
        answered 401 on every request, which reads as a server problem rather
        than as a build problem.

        Named rather than counted, because the caller refuses to start over
        this and "the built page has no token placeholder" sends whoever reads
        it to look at the page they were already thinking about — which is not
        the one at fault. It is also the number that decides whether a future
        page that genuinely needs no token is a build fault or a rule that
        wants narrowing, and that argument cannot be had against a boolean.
        """
        return tuple(
            sorted(url for url, html in self.pages.items() if _TOKEN_PLACEHOLDER not in html)
        )

    @property
    def carries_token_placeholder(self) -> bool:
        """Whether every page will actually receive a token.

        Every page, not just the root one: the page whose placeholder went
        missing is by definition the page nobody thought to open. A site with no
        pages answers no rather than vacuously yes, since that is a build
        failure too.
        """
        return bool(self.pages) and not self.pages_without_token


def default_site_root() -> Path:
    """Where the built site is, from the environment or from the checkout.

    Discovery walks up from this package looking for `web/dist`, which finds it
    in a source checkout and finds nothing in an installed wheel — a release
    ships the built site alongside and points at it with the environment
    variable rather than relying on a layout that only exists in development.
    """
    override = os.environ.get(SITE_ENV)
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "web" / "dist"
        if candidate.is_dir():
            return candidate
    return Path("web") / "dist"


def page_urls(relative: str) -> tuple[str, str | None]:
    """The URL a browser asks for a built page by, and the other form of it.

    Astro decides the file shape: `build.format: "directory"` writes
    `ping/index.html` and `"file"` writes `ping.html`, for the one link
    `/ping`. Both shapes are answered here rather than one being assumed,
    because the cost is three lines and the cost of assuming is a page served
    as bytes with its placeholder intact — which fails silently. The config
    states the shape anyway, so the two sides can be checked against each other
    instead of trusted to agree.

    The second URL is an alias for the same page, never a page of its own.
    """
    if relative == "index.html":
        return "/", None
    if relative.endswith("/index.html"):
        # `ping/index.html` -> `/ping/`, and `/ping` for the browser that drops
        # the slash on the way to it.
        directory = "/" + relative.removesuffix("index.html")
        return directory, directory.rstrip("/")
    return "/" + relative, "/" + relative.removesuffix(".html")


def load_site(root: Path | None = None) -> Site:
    """Read a built site into memory.

    Every `.html` becomes a page and nothing else can, so there is no route by
    which one is served as bytes with its placeholder intact.

    Raises :class:`SiteMissingError` with the command that produces it, rather than
    serving a blank page and leaving the reason to be guessed. A build carrying
    pages but no root one is reported the same way: it is not a site anything can
    navigate into, and the command that fixes it is the same one.
    """
    target = (root or default_site_root()).resolve()
    pages: dict[str, str] = {}
    aliases: dict[str, str] = {}
    assets: dict[str, tuple[bytes, str]] = {}
    skipped: dict[str, int] = {}

    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        relative = path.relative_to(target).as_posix()
        if suffix == ".html":
            html = path.read_text(encoding="utf-8")
            url, alias = page_urls(relative)
            pages[url] = html
            if alias is not None:
                aliases.setdefault(alias, html)
            continue
        content_type = _CONTENT_TYPES.get(suffix)
        if content_type is None:
            skipped[suffix or "(none)"] = skipped.get(suffix or "(none)", 0) + 1
            continue
        assets["/" + relative] = (path.read_bytes(), content_type)

    if "/" not in pages:
        raise SiteMissingError(
            f"no built page at {target}. Build it with:\n"
            "    cd web && npm install && npm run build\n"
            "The build is not committed, so a fresh checkout has to produce it once."
        )

    # Aliases last and only where nothing is: a page reached by its own URL is
    # never shadowed by another page's second form.
    for url, html in aliases.items():
        pages.setdefault(url, html)

    return Site(
        pages=pages,
        assets=assets,
        root=target,
        skipped=skipped,
        script_hashes={url: inline_script_hashes(html) for url, html in pages.items()},
    )


# Inline scripts only — one with a `src` is fetched and covered by 'self'.
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE
)


def inline_script_hashes(html: str) -> tuple[str, ...]:
    """`sha256-…` for each inline script, in document order.

    The hash covers the element's exact contents, so it has to be taken from
    the text that is actually served. Deduplicated because two identical
    scripts hash the same and a repeated source directive is noise.
    """
    seen: list[str] = []
    for match in _INLINE_SCRIPT.finditer(html):
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        value = f"sha256-{base64.b64encode(digest).decode('ascii')}"
        if value not in seen:
            seen.append(value)
    return tuple(seen)
