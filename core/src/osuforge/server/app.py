"""The HTTP and WebSocket surface.

Every request passes the checks in :mod:`osuforge.server.security` before it
reaches anything that reads a file, and the order matters: `Host` first, because
it is the layer that stops DNS rebinding and a rebound request must not get as
far as being told whether its token is valid.

# Why the pages themselves need no token

A browser navigating to a URL cannot send an `Authorization` header, so every
URL a person can type or click has to be reachable without one. That is safe and
it is not a compromise: a cross-site page can cause a navigation, but it cannot
read the response — the same-origin policy stops that — so it cannot get the
token out of the HTML. The page reads its own token and uses it for everything
after.

The set of URLs that are exempt is exactly the two tables handed in, both built
before the socket opened, and both refused at construction if anything in them
reaches into `/api` or `/ws`. So the exemption cannot grow to cover data by
someone adding a page, and there is no pattern to be matched per request and got
subtly wrong.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response

from osuforge.server.live import Broadcaster
from osuforge.server.security import Access

__all__ = ["build_app"]

_TOKEN_PREFIX = "Bearer "
_TOKEN_PLACEHOLDER = "__TOKEN__"
_RESERVED = ("/api", "/ws")
"""The namespaces nothing may be served from without a token."""

_TOKEN_SUBPROTOCOL = "osu-forge-token."
"""How a WebSocket carries the token.

A browser cannot set headers on a WebSocket handshake, and the usual workaround
is a query string — which ends up in every log and shell history that touches
the URL. The subprotocol field is the one header a browser will set on request,
so the token goes there.
"""


def _token_from(request: Request) -> str | None:
    authorisation = request.headers.get("authorization", "")
    if authorisation.startswith(_TOKEN_PREFIX):
        return authorisation[len(_TOKEN_PREFIX) :]
    return None


def _reserved(url: str) -> bool:
    # Case-folded, though the router matches case-sensitively and `/API/x` would
    # therefore never reach the `/api/x` handler. This is the check that decides
    # what is handed out without a token, and it is asked about a URL that came
    # from a file name on a case-preserving filesystem — so it answers about the
    # namespace rather than about one spelling of it, and is not left resting on
    # a second component agreeing with it.
    folded = url.casefold()
    return any(folded == prefix or folded.startswith(f"{prefix}/") for prefix in _RESERVED)


def _token_from_subprotocols(raw: str | None) -> str | None:
    for entry in (raw or "").split(","):
        candidate = entry.strip()
        if candidate.startswith(_TOKEN_SUBPROTOCOL):
            return candidate[len(_TOKEN_SUBPROTOCOL) :]
    return None


def build_app(
    access: Access,
    *,
    pages: dict[str, str],
    payloads: dict[str, Any],
    assets: dict[str, tuple[bytes, str]] | None = None,
    broadcaster: Broadcaster | None = None,
    policy: Callable[[str], str] | None = None,
    corpus: Callable[[], dict[str, Any] | None] | None = None,
) -> FastAPI:
    """Assemble the server.

    `pages` maps a URL path to HTML still holding the token placeholder, which
    is substituted into each response rather than once here, so the only copy of
    the token in this process is the one on its way out. `payloads` maps a
    replay name to a `ReplayPayload`, and `assets` maps a URL path to
    `(bytes, content type)`. All three are passed in rather than discovered
    here, because a server that goes looking at the filesystem on request is a
    server whose reach is decided by whatever asks it — and with the built site
    already in memory, path traversal has no mechanism to exist rather than
    being something a filter has to catch.

    `policy` is asked for the Content-Security-Policy of a path, per page rather
    than once for the site: each page ships its own inline script, and one
    policy naming every page's hashes would admit each of those scripts on all
    the other pages.

    `broadcaster` is where connected sockets are registered so a watcher can
    push a new play to them. Optional: without one the socket still answers
    requests and simply never volunteers anything.

    `corpus` returns the last corpus answer already computed, or `None` when
    there is none yet. A callable rather than a dictionary because the answer
    changes while the server runs, and a callable that only reads is what keeps
    the seconds of statistics behind it off the request path.

    Raises `ValueError` when a page or an asset claims a URL under `/api` or
    `/ws`. Nothing an Astro build emits lands there, and an exemption handed out
    inside the data surface is the one mistake this module has no second line of
    defence against, so it is refused rather than assumed impossible.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    static = assets or {}
    listeners = broadcaster or Broadcaster()

    # Everything in these two tables is served without a token, so this is the
    # one place the exemption could be widened by accident. Refused here, before
    # the socket opens, rather than by a per-request filter that would have to
    # keep being right for every path shape a browser can encode.
    trespassing = sorted(url for url in (*pages, *static) if _reserved(url))
    if trespassing:
        raise ValueError(
            "these are served without a token, so none may be under "
            f"{' or '.join(_RESERVED)}: {', '.join(trespassing)}"
        )

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not access.host_allowed(request.headers.get("host")):
            # Deliberately terse. A rebinding attempt has learned nothing from
            # this beyond that something refused it.
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not access.origin_allowed(request.headers.get("origin")):
            return JSONResponse({"error": "bad origin"}, status_code=403)

        # The pages and the assets they need are reachable without a token, for
        # the same reason: a browser fetching a stylesheet named by the HTML it
        # just loaded cannot attach a header either. Membership of the two
        # tables, not a prefix or a suffix — and both were checked above for
        # staying out of /api and /ws, which is where the data is.
        #
        # Read off the scope rather than `request.url.path`, which is the same
        # value only most of the time: the URL is rebuilt as a string from the
        # scope and split again, so a percent-encoded `?` or `#` in the path
        # decodes into a delimiter and everything after it is dropped. That
        # makes `/%3F../api/replays` read as `/` here — exempt — while the
        # router still matches the whole thing. Two answers to "which path is
        # this" is one more than a decision about who may see the data can
        # stand, and the router's is the one that decides what runs.
        path = request.scope["path"]
        exempt = path in pages or path in static
        if not exempt and not access.token.matches(_token_from(request)):
            return JSONResponse({"error": "unauthorised"}, status_code=401)

        response = await call_next(request)
        # No `Access-Control-Allow-Origin` at all. The pages this serves are
        # same-origin, and the header exists only to let other origins read the
        # response — which is the thing being prevented.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        if policy is not None:
            # As a header, not a meta element. `frame-ancestors` is ignored in
            # a meta and it is the directive that stops this page being framed,
            # so the meta form quietly protects less than it appears to.
            #
            # Asked for the requested path, so a page is sent the hashes of the
            # scripts it shipped with and nothing else is sent any.
            response.headers["Content-Security-Policy"] = policy(path)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/replays")
    async def replays() -> dict[str, Any]:
        return {
            "replays": [
                {"name": name, "header": payload.header} for name, payload in payloads.items()
            ]
        }

    @app.get("/api/corpus")
    async def corpus_answer() -> dict[str, Any]:
        # 404 rather than an empty object when there is nothing to serve. An
        # empty object reads as "a corpus with nothing in it", which is a
        # conclusion; the honest status is that no answer exists here.
        found = corpus() if corpus is not None else None
        if found is None:
            raise HTTPException(status_code=404, detail="no corpus prepared")
        return found

    @app.get("/api/replays/{name}/header")
    async def header(name: str) -> dict[str, Any]:
        payload = payloads.get(name)
        if payload is None:
            raise HTTPException(status_code=404, detail="no such replay")
        result: dict[str, Any] = payload.header
        return result

    @app.get("/api/replays/{name}/frames")
    async def frames(name: str) -> Response:
        payload = payloads.get(name)
        if payload is None:
            raise HTTPException(status_code=404, detail="no such replay")
        return Response(content=payload.frames, media_type="application/octet-stream")

    @app.get("/api/replays/{name}/paths")
    async def paths(name: str) -> Response:
        payload = payloads.get(name)
        if payload is None:
            raise HTTPException(status_code=404, detail="no such replay")
        return Response(content=payload.paths, media_type="application/octet-stream")

    # Deliberately not `async`. These two are the only handlers that touch the
    # disk, and a blocking read inside a coroutine holds the event loop for its
    # whole duration — every other request and the live socket's broadcasts
    # among them, for as long as a several-megabyte read off a cold spinning
    # disk takes. Declared synchronously, FastAPI runs them in its threadpool
    # instead, which is where a blocking read belongs.
    @app.get("/api/replays/{name}/background")
    def background(name: str) -> Response:
        # The one response read from disk at request time — and the path was
        # resolved when the payload was prepared, so the request still only
        # names a replay. Holding megabytes of JPEG per payload resident would
        # let the songs folder size the server's memory instead.
        payload = payloads.get(name)
        found = getattr(payload, "background", None)
        if payload is None or found is None:
            raise HTTPException(status_code=404, detail="no background")
        try:
            body = found.read_bytes()
        except OSError as exc:
            # Deleted or unreadable since startup. The map has no background
            # any more, which is the same answer as never having had one.
            raise HTTPException(status_code=404, detail="no background") from exc
        suffix = found.suffix.lower()
        kinds = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        return Response(content=body, media_type=kinds.get(suffix, "application/octet-stream"))

    @app.get("/api/replays/{name}/audio")
    def audio(name: str) -> Response:
        # Read from disk at request time like the background, and resolved when
        # the payload was prepared for the same reason: the request names a
        # replay, never a file.
        #
        # The whole track in one response, with no range support. The page
        # fetches it into a blob before it plays anything, so a partial response
        # would only add a code path neither side uses.
        payload = payloads.get(name)
        found = getattr(payload, "audio", None)
        if payload is None or found is None:
            raise HTTPException(status_code=404, detail="no audio")
        try:
            body = found.read_bytes()
        except OSError as exc:
            # Deleted or unreadable since startup, which is the same answer to
            # the page as a map that never had a track beside it.
            raise HTTPException(status_code=404, detail="no audio") from exc
        suffix = found.suffix.lower()
        # mp3 and ogg are what beatmaps in the wild carry; wav is here because
        # the format allows it, and anything else is served as bytes rather
        # than named a kind it might not be.
        kinds = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav"}
        return Response(content=body, media_type=kinds.get(suffix, "application/octet-stream"))

    @app.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        # The middleware above does not run for WebSockets, so the same checks
        # are made here rather than assumed. Refusing before `accept` means the
        # handshake fails outright and nothing is ever connected.
        if not access.host_allowed(websocket.headers.get("host")):
            await websocket.close(code=1008)
            return
        if not access.origin_allowed(websocket.headers.get("origin")):
            await websocket.close(code=1008)
            return

        offered = websocket.headers.get("sec-websocket-protocol")
        presented = _token_from_subprotocols(offered)
        if not access.token.matches(presented):
            await websocket.close(code=1008)
            return

        # Echoing the subprotocol back is required, or the browser rejects the
        # connection it just opened.
        await websocket.accept(subprotocol=f"{_TOKEN_SUBPROTOCOL}{presented}")
        listeners.add(websocket)
        try:
            while True:
                request = await websocket.receive_json()
                name = request.get("replay")
                payload = payloads.get(name)
                if payload is None:
                    await websocket.send_json({"error": "no such replay", "replay": name})
                    continue
                await websocket.send_json({"header": payload.header})
                await websocket.send_bytes(payload.frames)
        except Exception:
            # A closed socket arrives as an exception. Nothing here owns state
            # that needs unwinding, so the connection simply ends.
            return
        finally:
            # Unregistered on every path, including the one that returns above.
            # A socket left in the set is one the broadcaster tries to write to
            # for the rest of the run, and it only discovers the problem by
            # failing.
            listeners.discard(websocket)

    # Registered last, deliberately: a catch-all declared earlier would shadow
    # every named route above it. Both lookups are dicts built before the socket
    # opened, so a request for `../../secrets` finds no key — path traversal has
    # no mechanism here rather than being caught by a filter that has to be
    # right every time. One route for pages and assets both, because the table a
    # URL is found in is what decides how it is served, and two routes would be
    # two chances for a page to be answered as bytes.
    @app.get("/{path:path}", include_in_schema=False)
    async def served(path: str) -> Response:
        url = "/" + path
        html = pages.get(url)
        if html is not None:
            return HTMLResponse(html.replace(_TOKEN_PLACEHOLDER, access.token.value))
        found = static.get(url)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        body, content_type = found
        return Response(content=body, media_type=content_type)

    return app
