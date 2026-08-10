"""What was already analysed, so a restart does not forget it.

Two costs made the served corpus smaller than the player's history. Every
start re-simulated the newest replays from scratch, and the corpus could not
reach past the payload cap — frames for playback cost half a megabyte per
replay, so only the newest few dozen were prepared, and the corpus quietly
inherited that ceiling even though its own entries are a few kilobytes of hit
errors. A player with five hundred plays was shown a twenty-five-play corpus
and a page that took as long to start as the statistics took to run.

This cache separates the two. Playback payloads stay capped and in memory;
the *reduction* of every play the tool has ever analysed — the hit errors,
the screen's verdict, the timestamps the sessions come from — persists here,
so the corpus is restored whole at startup and grows run over run.

# A cache, which is exactly why it is not the store

:mod:`osuforge.store.db` holds what a person typed and cannot be rebuilt;
deleting it loses information. Everything in this file is derived from
replays still on disk and can be rebuilt by re-analysing them. Deleting it
costs minutes, not facts — which is why it is a separate file rather than
tables beside the devices, and why every reader here treats a damaged row as
a miss rather than an error.

# Keyed by what would change the answer

A row is only believed when the file's name, size and mtime all match — a
replaced replay re-analyses — and when it was written by this version of the
tool. The simulator's decisions are part of the number: hit errors cached by
an older build are measurements made with different rules, and mixing them
with fresh ones would produce a corpus no single version of the code stands
behind. Rows from other versions are dropped on open.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from osuforge import __version__

__all__ = ["CACHE_ENV", "AnalysisCache", "default_cache_path", "open_cache"]

CACHE_ENV = "OSU_FORGE_CACHE"
"""Environment variable overriding where the cache lives."""

_SCHEMA = 2
"""Bumped together with any change to what `facts` carries."""

_TOOL = f"{__version__}/{_SCHEMA}"
"""What must match for a row to be believed. The tool version is part of the
measurement, not metadata about it."""


def default_cache_path() -> Path:
    """`%LOCALAPPDATA%\\osu-forge\\analysis-cache.db`, or the override.

    Beside the journal and the store, outside the osu! install — and named for
    what it is, so someone clearing space knows this one is safe to delete.
    """
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "analysis-cache.db"


class AnalysisCache:
    """The persisted reductions, opened once and shared across threads.

    One connection behind one lock, because the writers are the startup scan
    and the watcher's worker threads and every operation here is a
    single-row read or write — contention is not the problem this needs to
    solve, and a connection per call would pay the open on every play.

    Every method degrades to "not cached" rather than raising: a cache that
    can take the server down is more expensive than no cache at all.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plays (
                    name     TEXT PRIMARY KEY,
                    size     INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    tool     TEXT NOT NULL,
                    facts    TEXT NOT NULL
                )
                """
            )
            # Rows written by another build are measurements made under
            # different rules. Dropped now, in one sweep, so no lookup has to
            # reason about them.
            connection.execute("DELETE FROM plays WHERE tool != ?", (_TOOL,))
            connection.commit()
            self._connection = connection
        except OSError, sqlite3.Error:
            # A missing directory, a locked file, a corrupt database: the
            # server runs without a cache and says so once, at startup,
            # through `available`.
            if connection is not None:
                connection.close()
            self._connection = None

    @property
    def available(self) -> bool:
        return self._connection is not None

    def __len__(self) -> int:
        if self._connection is None:
            return 0
        try:
            with self._lock:
                row = self._connection.execute("SELECT COUNT(*) FROM plays").fetchone()
            return int(row[0])
        except sqlite3.Error:
            return 0

    def lookup(self, name: str, *, size: int, mtime_ns: int) -> dict[str, Any] | None:
        """The cached reduction, or `None` when it must be recomputed."""
        if self._connection is None:
            return None
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT facts FROM plays WHERE name = ? AND size = ? AND mtime_ns = ?",
                    (name, size, mtime_ns),
                ).fetchone()
            if row is None:
                return None
            facts = json.loads(row[0])
        except sqlite3.Error, json.JSONDecodeError:
            return None
        if not isinstance(facts, dict):
            return None
        return facts

    def put(self, name: str, *, size: int, mtime_ns: int, facts: dict[str, Any]) -> None:
        if self._connection is None:
            return
        try:
            payload = json.dumps(facts, ensure_ascii=False)
            with self._lock:
                self._connection.execute(
                    """
                    INSERT INTO plays (name, size, mtime_ns, tool, facts)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        tool = excluded.tool,
                        facts = excluded.facts
                    """,
                    (name, size, mtime_ns, _TOOL, payload),
                )
                self._connection.commit()
        except TypeError, ValueError, sqlite3.Error:
            # A reduction that cannot be stored is recomputed next start.
            # Costs minutes later rather than the corpus now.
            return

    def close(self) -> None:
        if self._connection is not None:
            with self._lock:
                self._connection.close()
            self._connection = None


@contextmanager
def open_cache(path: Path | None = None) -> Iterator[AnalysisCache]:
    """`AnalysisCache`, closed afterwards. For callers with a clear lifetime;
    the server holds one for the life of the process instead."""
    cache = AnalysisCache(path)
    try:
        yield cache
    finally:
        cache.close()
