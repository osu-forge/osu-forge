"""Noticing a new play, and telling the page about it.

`forge live` and `forge serve` were each half of what a player wants while
sitting at the game. The static page updates and throws away scroll position and
every open section doing it; the served page keeps all of that and never learns
that a new play happened. This is the missing direction on the WebSocket: the
server pushes, the page inserts, and nothing the user was looking at re-renders.

# What is pushed, and what is not

Only the header. It is a few kilobytes and it is everything the list needs to
show the play. The frames are half a megabyte and the page does not need them
until someone selects that replay, so they stay behind the existing endpoint.

# A replay file exists before it is finished

osu! creates the `.osr` and then writes it, so a watcher that parses the moment
it appears reads a truncated file. That is not corruption and must not be
recorded as such: the file is retried on later passes and only given up on after
it has been failing for long enough to be a real problem. Treating the first
failure as final would silently drop the play the user just finished — the one
they are most likely to be waiting for.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["POLL_SECONDS", "SETTLE_ATTEMPTS", "Broadcaster", "Watcher"]

POLL_SECONDS = 2.0
"""How often the replay folder is listed.

A directory listing is cheap and a play lasts minutes, so this is not a hot
loop. Faster would not make a play appear sooner — the file only exists once
osu! has written it.
"""

SETTLE_ATTEMPTS = 5
"""How many passes a file may fail to parse before it is given up on.

Ten seconds at the default interval. A file being written is the ordinary
reason for a failure here, and a genuinely corrupt replay stops being retried
rather than being retried forever.
"""


@dataclass
class Broadcaster:
    """Every page currently listening.

    Sockets are held in a set rather than a list because the only operations
    are add, discard and iterate, and a page that reloads twice must not end up
    receiving everything twice.
    """

    sockets: set[Any] = field(default_factory=set)

    def add(self, socket: Any) -> None:
        self.sockets.add(socket)

    def discard(self, socket: Any) -> None:
        self.sockets.discard(socket)

    async def send(self, message: dict[str, Any]) -> int:
        """Send to everyone still connected. Returns how many received it.

        A socket that raises has gone away — a closed tab, a reload mid-send —
        and is dropped rather than retried. Collected first and discarded after,
        because mutating the set while iterating it is a different bug that
        looks like this one.
        """
        gone: list[Any] = []
        delivered = 0
        for socket in list(self.sockets):
            try:
                await socket.send_json(message)
                delivered += 1
            except Exception:
                gone.append(socket)
        for socket in gone:
            self.sockets.discard(socket)
        return delivered


@dataclass
class Watcher:
    """Polls a folder for replays that were not there before."""

    folder: Path
    build: Callable[[Path], Awaitable[dict[str, Any] | None]]
    """Turn a path into a message to broadcast, or `None` to retry it later."""

    seen: set[str] = field(default_factory=set)
    failures: dict[str, int] = field(default_factory=dict)
    abandoned: set[str] = field(default_factory=set)

    def prime(self, names: Iterable[str]) -> None:
        """Mark what is already known, so a start is not a flood of old plays."""
        self.seen.update(names)

    def candidates(self) -> list[Path]:
        try:
            found = list(self.folder.glob("*.osr"))
        except OSError:
            # The folder can vanish — a drive unplugged, osu! reinstalled.
            # A missing folder is nothing new to report, not a crash.
            return []
        fresh = (
            path for path in found if path.name not in self.seen and path.name not in self.abandoned
        )
        return sorted(fresh, key=lambda path: path.stat().st_mtime)

    async def poll(self) -> list[dict[str, Any]]:
        """One pass. Returns the messages for whatever became readable."""
        messages: list[dict[str, Any]] = []
        for path in self.candidates():
            message = await self.build(path)
            if message is None:
                count = self.failures.get(path.name, 0) + 1
                self.failures[path.name] = count
                if count >= SETTLE_ATTEMPTS:
                    # Still unreadable long after it stopped being written.
                    # Abandoned rather than retried forever, and named so the
                    # difference between "not finished" and "not a replay" is
                    # visible in the log rather than inferred from silence.
                    self.abandoned.add(path.name)
                    self.failures.pop(path.name, None)
                continue
            self.failures.pop(path.name, None)
            self.seen.add(path.name)
            messages.append(message)
        return messages

    async def run(self, broadcaster: Broadcaster, *, interval: float = POLL_SECONDS) -> None:
        """Poll until cancelled, broadcasting whatever appears.

        Exceptions from one pass do not end the loop. A watcher that dies on a
        transient filesystem error leaves a page that looks connected and never
        updates again, which is worse than a pass that produced nothing.
        """
        while True:
            with contextlib.suppress(Exception):
                for message in await self.poll():
                    await broadcaster.send(message)
            await asyncio.sleep(interval)
