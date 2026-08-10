"""The analysis cache: what it remembers, and when it refuses to.

The refusals are the tests that matter. A cache that returns a stale row
poisons the corpus silently; a cache that raises takes the server down. Both
failure modes are worse than recomputing, so every doubtful case must read as
a miss.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from osuforge.server.cache import open_cache

FACTS = {
    "beatmap_hash": "abc123",
    "beatmap": "Artist - Title [Hard]",
    "played_at": "2026-08-01T19:00:00+00:00",
    "errors": [1.5, -2.25, 0.0],
    "miss_rate": 0.02,
    "accuracy": 0.97,
    "breaks": 1,
    "agreement": "exact",
    "agreement_reason": "all judgements reproduce",
    "fractional_windows": False,
}


class TestRoundTrip:
    def test_what_was_put_comes_back(self, tmp_path: Path) -> None:
        with open_cache(tmp_path / "cache.db") as cache:
            assert cache.available
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
            assert cache.lookup("a.osr", size=1000, mtime_ns=42) == FACTS
            assert len(cache) == 1

    def test_it_survives_a_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        with open_cache(path) as cache:
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
        with open_cache(path) as reopened:
            assert reopened.lookup("a.osr", size=1000, mtime_ns=42) == FACTS

    def test_a_reput_replaces(self, tmp_path: Path) -> None:
        with open_cache(tmp_path / "cache.db") as cache:
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
            fresh = {**FACTS, "accuracy": 0.99}
            cache.put("a.osr", size=1001, mtime_ns=43, facts=fresh)
            assert cache.lookup("a.osr", size=1001, mtime_ns=43) == fresh
            assert len(cache) == 1


class TestMisses:
    def test_a_changed_file_is_a_miss(self, tmp_path: Path) -> None:
        # A replaced replay must re-analyse; believing the old reduction would
        # attach one file's numbers to another file's name.
        with open_cache(tmp_path / "cache.db") as cache:
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
            assert cache.lookup("a.osr", size=1001, mtime_ns=42) is None
            assert cache.lookup("a.osr", size=1000, mtime_ns=43) is None

    def test_rows_from_another_build_are_dropped_on_open(self, tmp_path: Path) -> None:
        # The simulator's decisions are part of the number. A row written by a
        # different version is a measurement made under different rules.
        path = tmp_path / "cache.db"
        with open_cache(path) as cache:
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE plays SET tool = 'someone-else/9'")
        connection.commit()
        connection.close()
        with open_cache(path) as reopened:
            assert reopened.lookup("a.osr", size=1000, mtime_ns=42) is None
            assert len(reopened) == 0

    def test_a_corrupt_row_is_a_miss_not_a_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        with open_cache(path) as cache:
            cache.put("a.osr", size=1000, mtime_ns=42, facts=FACTS)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE plays SET facts = 'not json {'")
        connection.commit()
        connection.close()
        with open_cache(path) as reopened:
            assert reopened.lookup("a.osr", size=1000, mtime_ns=42) is None

    def test_a_corrupt_database_means_no_cache_not_no_server(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        path.write_bytes(b"this is not a database")
        with open_cache(path) as cache:
            assert not cache.available
            # Every operation degrades to a no-op rather than raising.
            cache.put("a.osr", size=1, mtime_ns=1, facts=FACTS)
            assert cache.lookup("a.osr", size=1, mtime_ns=1) is None
            assert len(cache) == 0
