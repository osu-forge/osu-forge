"""The HTTP and WebSocket surface.

Every request passes the checks in :mod:`osuforge.server.security` before it
reaches anything that reads a file, and the order matters: `Host` first, because
it is the layer that stops DNS rebinding and a rebound request must not get as
far as being told whether its token is valid.

# Why the page itself needs no token

A browser navigating to a URL cannot send an `Authorization` header, so `GET /`
has to be reachable without one. That is safe and it is not a compromise: a
cross-site page can cause a navigation, but it cannot read the response — the
same-origin policy stops that — so it cannot get the token out of the HTML. The
page reads its own token and uses it for everything after.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response

from osuforge.server.security import Access

__all__ = ["build_app"]

_TOKEN_PREFIX = "Bearer "
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


def _token_from_subprotocols(raw: str | None) -> str | None:
    for entry in (raw or "").split(","):
        candidate = entry.strip()
        if candidate.startswith(_TOKEN_SUBPROTOCOL):
            return candidate[len(_TOKEN_SUBPROTOCOL) :]
    return None


def build_app(
    access: Access,
    *,
    page: str,
    payloads: dict[str, Any],
    assets: dict[str, tuple[bytes, str]] | None = None,
) -> FastAPI:
    """Assemble the server.

    `payloads` maps a replay name to a `ReplayPayload`, and `assets` maps a URL
    path to `(bytes, content type)`. Both are passed in rather than discovered
    here, because a server that goes looking at the filesystem on request is a
    server whose reach is decided by whatever asks it — and with the built site
    already in memory, path traversal has no mechanism to exist rather than
    being something a filter has to catch.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    static = assets or {}

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

        # The page and the assets it needs are reachable without a token, for
        # the same reason: a browser fetching a stylesheet named by the HTML it
        # just loaded cannot attach a header either. Nothing under /api or /ws
        # is exempt, and those are where the data is.
        path = request.url.path
        exempt = path == "/" or path in static
        if not exempt and not access.token.matches(_token_from(request)):
            return JSONResponse({"error": "unauthorised"}, status_code=401)

        response = await call_next(request)
        # No `Access-Control-Allow-Origin` at all. The pages this serves are
        # same-origin, and the header exists only to let other origins read the
        # response — which is the thing being prevented.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(page.replace("__TOKEN__", access.token.value))

    @app.get("/api/replays")
    async def replays() -> dict[str, Any]:
        return {
            "replays": [
                {"name": name, "header": payload.header} for name, payload in payloads.items()
            ]
        }

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

    # Registered last, deliberately: a catch-all declared earlier would shadow
    # every named route above it. The lookup is a dict built before the socket
    # opened, so a request for `../../secrets` finds no key — path traversal has
    # no mechanism here rather than being caught by a filter that has to be
    # right every time.
    @app.get("/{path:path}", include_in_schema=False)
    async def asset(path: str) -> Response:
        found = static.get("/" + path)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        body, content_type = found
        return Response(content=body, media_type=content_type)

    return app
