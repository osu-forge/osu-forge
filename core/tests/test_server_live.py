"""Noticing a new play, and telling the page about it.

The point of these is the retry. osu! creates the `.osr` and then writes it, so
the first parse of the play a user just finished reads a truncated file — and
that is the one play they are most likely to be waiting for.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from osuforge.server.live import SETTLE_ATTEMPTS, Broadcaster, Watcher


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Drive a coroutine from a synchronous test.

    Rather than adding an async test plugin. This package's dependencies pass a
    licence gate in CI, and a test runner extension is a poor thing to spend
    that on when three lines do the job.
    """
    return asyncio.run(coroutine)


class FakeSocket:
    """Records what it was sent, and can be told to fail like a closed tab."""

    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fails = fails

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.fails:
            raise ConnectionResetError("gone")
        self.sent.append(message)


class TestBroadcaster:
    def test_everyone_connected_receives_it(self) -> None:
        broadcaster = Broadcaster()
        one, two = FakeSocket(), FakeSocket()
        broadcaster.add(one)
        broadcaster.add(two)
        assert run(broadcaster.send({"event": "replay"})) == 2
        assert one.sent == two.sent == [{"event": "replay"}]

    def test_a_socket_that_has_gone_away_is_dropped(self) -> None:
        # A closed tab or a reload mid-send. Retrying it forever would make
        # every later broadcast slower and none of them more correct.
        broadcaster = Broadcaster()
        alive, dead = FakeSocket(), FakeSocket(fails=True)
        broadcaster.add(alive)
        broadcaster.add(dead)
        assert run(broadcaster.send({"a": 1})) == 1
        assert dead not in broadcaster.sockets
        assert alive in broadcaster.sockets

    def test_the_same_socket_added_twice_receives_once(self) -> None:
        # A page that reloads must not end up getting everything in duplicate.
        broadcaster = Broadcaster()
        socket = FakeSocket()
        broadcaster.add(socket)
        broadcaster.add(socket)
        run(broadcaster.send({"a": 1}))
        assert len(socket.sent) == 1

    def test_sending_to_nobody_is_not_an_error(self) -> None:
        assert run(Broadcaster().send({"a": 1})) == 0


def touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"not a real replay")
    return path


async def naming(path: Path) -> dict[str, Any]:
    return {"name": path.name}


class TestWatcher:
    def test_a_new_file_becomes_a_message(self, tmp_path: Path) -> None:
        watcher = Watcher(folder=tmp_path, build=naming)
        touch(tmp_path, "new.osr")
        assert run(watcher.poll()) == [{"name": "new.osr"}]

    def test_what_was_already_there_is_not_reported(self, tmp_path: Path) -> None:
        # Opening the page must not be a flood of plays from last week.
        touch(tmp_path, "old.osr")
        watcher = Watcher(folder=tmp_path, build=naming)
        watcher.prime(path.name for path in tmp_path.glob("*.osr"))
        assert run(watcher.poll()) == []

    def test_a_file_is_reported_once(self, tmp_path: Path) -> None:
        watcher = Watcher(folder=tmp_path, build=naming)
        touch(tmp_path, "one.osr")
        assert len(run(watcher.poll())) == 1
        assert run(watcher.poll()) == []

    def test_a_file_still_being_written_is_retried_not_dropped(self, tmp_path: Path) -> None:
        # The case that matters. osu! creates the file and then writes it, so
        # the first parse of the play just finished fails — and treating that
        # as corruption would silently lose the play the user is waiting for.
        attempts = 0

        async def build(path: Path) -> dict[str, Any] | None:
            nonlocal attempts
            attempts += 1
            return None if attempts < 3 else {"name": path.name}

        watcher = Watcher(folder=tmp_path, build=build)
        touch(tmp_path, "writing.osr")
        assert run(watcher.poll()) == []
        assert run(watcher.poll()) == []
        assert run(watcher.poll()) == [{"name": "writing.osr"}]

    def test_something_that_never_parses_is_eventually_given_up_on(self, tmp_path: Path) -> None:
        calls = 0

        async def build(path: Path) -> dict[str, Any] | None:
            nonlocal calls
            calls += 1
            return None

        watcher = Watcher(folder=tmp_path, build=build)
        touch(tmp_path, "broken.osr")
        for _ in range(SETTLE_ATTEMPTS + 3):
            run(watcher.poll())
        assert calls == SETTLE_ATTEMPTS, "retried until the limit, then left alone"
        assert "broken.osr" in watcher.abandoned

    def test_a_missing_folder_reports_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        # A drive unplugged or osu! reinstalled mid-session.
        watcher = Watcher(folder=tmp_path / "gone", build=naming)
        assert run(watcher.poll()) == []

    def test_only_replays_are_considered(self, tmp_path: Path) -> None:
        touch(tmp_path, "notes.txt")
        touch(tmp_path, "play.osr")
        watcher = Watcher(folder=tmp_path, build=naming)
        assert run(watcher.poll()) == [{"name": "play.osr"}]


class TestLoop:
    def test_it_broadcasts_and_survives_a_failing_pass(self, tmp_path: Path) -> None:
        # A watcher that dies on a transient error leaves a page that looks
        # connected and never updates again, which is worse than a pass that
        # produced nothing.
        passes = 0

        async def build(path: Path) -> dict[str, Any]:
            nonlocal passes
            passes += 1
            if passes == 1:
                raise OSError("transient")
            return {"event": "replay", "name": path.name}

        broadcaster = Broadcaster()
        socket = FakeSocket()
        broadcaster.add(socket)
        watcher = Watcher(folder=tmp_path, build=build)
        touch(tmp_path, "play.osr")

        async def drive() -> None:
            task = asyncio.get_running_loop().create_task(watcher.run(broadcaster, interval=0.01))
            for _ in range(300):
                if socket.sent:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        run(drive())
        assert socket.sent == [{"event": "replay", "name": "play.osr"}]
        assert passes >= 2, "the first pass raised and the loop kept going"
