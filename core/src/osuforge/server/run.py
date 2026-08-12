"""Starting the server, and refusing to start it somewhere unexpected.

Everything the four security layers protect is decided before a request
arrives: which port, which token, which interface. This is where those are
chosen, so the choices are here rather than spread through the call site.

# A taken port is a failure, not a reason to move

The obvious convenience is to try the next port when one is busy. It is the
wrong behaviour and the reason is the whole point of the port being fixed.

`runtime.json` publishes where to connect, but a client that has one cached, or
a user with a bookmark, or an overlay configured by hand, all go to the port
they knew about. If this server quietly moves, that port is now either nothing
or **something else** — and the ports next to the default belong to other tools
in this exact category. Connecting an overlay to the wrong program is worse
than an overlay that does not connect, because it looks like it worked.

So a busy port raises, with the port named. Moving is a decision for whoever
runs it, expressed by passing a different one.

# The bind is checked before uvicorn is asked

uvicorn's own failure on a taken port arrives as a log line and an exit, which
is hard to turn into a message worth reading. Binding a socket first costs a
millisecond and turns it into an exception with a sentence attached.

The interface is `127.0.0.1` and is not configurable. Nothing here is intended
to be reachable from another machine, and a flag that made it so would be the
one line of this file that undid the rest of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from osuforge.server.app import build_app
from osuforge.server.assets import Site
from osuforge.server.live import Broadcaster, Watcher
from osuforge.server.security import (
    DEFAULT_PORT,
    RESERVED_PORTS,
    Access,
    SessionToken,
    runtime_file,
    write_runtime_file,
)

__all__ = ["INTERFACE", "ServerError", "prepare", "serve"]

INTERFACE = "127.0.0.1"
"""The only interface this binds. Deliberately not a parameter."""


class ServerError(RuntimeError):
    """The server could not start where it was asked to."""


def _check_port(port: int) -> None:
    if port in RESERVED_PORTS:
        raise ServerError(
            f"port {port} belongs to another tool of this kind — tosu uses 24050 and "
            "StreamCompanion 20727. Sharing it would let an overlay talk to the wrong "
            "program, which is worse than not connecting at all."
        )
    if not 1024 <= port <= 65535:
        raise ServerError(f"port {port} is outside the range this may bind (1024-65535)")

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR. The question is whether something is listening here
        # right now, and reuse is exactly what would hide that on some
        # platforms.
        probe.bind((INTERFACE, port))
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(exc, "winerror", None) == 10048:
            raise ServerError(
                f"port {port} is already in use. It is not moved automatically: a client "
                "with the old port cached would then reach whatever else is there, and "
                "the neighbouring ports belong to other tools. Stop what is holding it, "
                "or pass a different port."
            ) from exc
        raise ServerError(f"could not bind {INTERFACE}:{port}: {exc}") from exc
    finally:
        probe.close()


@contextmanager
def prepare(
    *,
    port: int = DEFAULT_PORT,
    runtime_path: Path | None = None,
) -> Iterator[Access]:
    """Claim a port, publish the runtime file, and clean up on the way out.

    The file holds a live credential for this run only, so it is removed when
    the run ends. A stale one left behind sends the next client to a port that
    is no longer answering, carrying a token that no longer means anything —
    and the honest failure is "nothing is running", not a refused request.

    A hard kill leaves one anyway; nothing runs on `SIGKILL`. What that costs is
    bounded and worth stating rather than papering over: a client in the window
    between the kill and the next start finds a port with nothing on it and
    fails to connect, which is the same outcome as no file at all. The next run
    truncates it before writing, so the stale token is never one of two.
    """
    _check_port(port)
    access = Access(port=port, token=SessionToken.generate())
    target = write_runtime_file(access, path=runtime_path)
    try:
        yield access
    finally:
        target.unlink(missing_ok=True)


def serve(
    *,
    site: Site,
    payloads: dict[str, Any],
    port: int = DEFAULT_PORT,
    runtime_path: Path | None = None,
    log_level: str = "warning",
    watcher: Watcher | None = None,
    broadcaster: Broadcaster | None = None,
    corpus: Callable[[], dict[str, Any] | None] | None = None,
) -> None:
    """Run until interrupted. Blocks.

    `site` and `payloads` are both read before this is called, matching
    :func:`osuforge.server.app.build_app`: a server that goes looking at the
    filesystem when asked is a server whose reach is decided by whoever asks.

    `watcher` is the one exception, and a deliberate one: watching for a new
    play is the whole point of leaving the page open. It polls a folder chosen
    when the command started, not one a request can name.

    `broadcaster` may be passed in by a caller that needs to push its own
    events — a corpus that changed, not just a play that finished. Left unset,
    one is created here and only the watcher speaks.
    """
    import uvicorn

    with prepare(port=port, runtime_path=runtime_path) as access:
        listeners = broadcaster if broadcaster is not None else Broadcaster()
        app = build_app(
            access,
            pages=site.pages,
            payloads=payloads,
            assets=site.assets,
            broadcaster=listeners,
            policy=site.policy,
            corpus=corpus,
        )

        if watcher is not None:

            @app.on_event("startup")
            async def _start_watching() -> None:
                # Started here rather than before uvicorn, because the loop it
                # runs on is the one uvicorn creates.
                task = asyncio.get_running_loop().create_task(watcher.run(listeners))
                app.state.watcher_task = task

            @app.on_event("shutdown")
            async def _stop_watching() -> None:
                task = getattr(app.state, "watcher_task", None)
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        uvicorn.run(
            app,
            host=INTERFACE,
            port=access.port,
            log_level=log_level,
            # No access log. Every line would carry a URL that this design
            # deliberately keeps the token out of, and the one thing worth
            # logging locally is the failure, which raises instead.
            access_log=False,
        )


def runtime_location() -> Path:
    """Where the port and token are published, for a message that says so."""
    return runtime_file()
