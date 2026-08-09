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

`index.html` contains the literal `__TOKEN__`, which Astro emits from the page
source and which is replaced when the page is served. It is in the HTML rather
than fetched because a browser navigating to a URL cannot send an
`Authorization` header, and a cross-site page can cause that navigation but
cannot read the response.

Substitution happens per request rather than at load, so the same loaded site
can be served by a run with a different token — and so the token is never
resident in the asset table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SITE_ENV", "Site", "SiteMissingError", "default_site_root", "load_site"]

SITE_ENV = "OSU_FORGE_SITE"
"""Environment variable pointing at a built site, overriding discovery."""

_TOKEN_PLACEHOLDER = "__TOKEN__"

# Only what a static site is made of. An extension absent from this map is not
# served at all rather than served as a guess: `application/octet-stream` on a
# file the browser expected to execute produces a blank page and an hour of
# looking in the wrong place.
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
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

    index: str
    """`index.html`, still holding the token placeholder."""

    assets: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    """URL path to `(bytes, content type)`. Paths begin with `/`."""

    root: Path = Path()
    skipped: dict[str, int] = field(default_factory=dict)
    """Extensions found but not served, by count."""

    def page(self, token: str) -> str:
        return self.index.replace(_TOKEN_PLACEHOLDER, token)

    @property
    def carries_token_placeholder(self) -> bool:
        """Whether the page will actually receive a token.

        A build whose placeholder went missing would serve a page that fetches
        without one and fails on every request with a 401 — which looks like a
        server problem rather than a build problem.
        """
        return _TOKEN_PLACEHOLDER in self.index


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


def load_site(root: Path | None = None) -> Site:
    """Read a built site into memory.

    Raises :class:`SiteMissingError` with the command that produces it, rather than
    serving a blank page and leaving the reason to be guessed.
    """
    target = (root or default_site_root()).resolve()
    index_path = target / "index.html"
    if not index_path.is_file():
        raise SiteMissingError(
            f"no built page at {target}. Build it with:\n"
            "    cd web && npm install && npm run build\n"
            "The build is not committed, so a fresh checkout has to produce it once."
        )

    index = index_path.read_text(encoding="utf-8")
    assets: dict[str, tuple[bytes, str]] = {}
    skipped: dict[str, int] = {}

    for path in sorted(target.rglob("*")):
        if not path.is_file() or path == index_path:
            continue
        content_type = _CONTENT_TYPES.get(path.suffix.lower())
        if content_type is None:
            skipped[path.suffix.lower() or "(none)"] = (
                skipped.get(path.suffix.lower() or "(none)", 0) + 1
            )
            continue
        url = "/" + path.relative_to(target).as_posix()
        assets[url] = (path.read_bytes(), content_type)

    return Site(index=index, assets=assets, root=target, skipped=skipped)
