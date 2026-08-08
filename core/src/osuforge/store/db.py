"""The local database, and the migrations that let it grow.

# Why a database rather than a settings file

Because the shape is going to change, and the change is already visible from
here. A tablet is a device kind that does not exist yet. A mouse has a vendor
and a model worth recording. A second vendor's sensor presets should be data
rather than a code change. A file with one hardcoded shape meets each of those
with either a breaking change or another optional key that nothing can audit.

So devices are rows carrying a `kind` and a JSON attribute bag. Adding tablets
adds a kind, not a column; recording a mouse model adds a key to the bag, not a
migration. What the schema pins down is only what every device has in common.

# Migrations, and refusing to guess

:data:`SCHEMA_VERSION` is the shape this build understands. On open, an older
file is migrated forward inside a transaction; a **newer** file is refused
outright rather than read for the fields that happen to be recognisable. A
partial read of a file written by a later build is the failure mode this whole
design exists to avoid — it would silently drop whatever that build added and
then write the file back without it.

# Not a cache

Everything here was typed by a person and cannot be recovered by rescanning
anything. That is the difference between this and the journal, which is derived
from replays and could be rebuilt from them. Deleting this file loses
information; it is written whole rather than appended to, because it is current
configuration and its history is not evidence for anything.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DB_ENV",
    "SCHEMA_VERSION",
    "DeviceRow",
    "Store",
    "StoreError",
    "default_db_path",
    "open_store",
]

DB_ENV = "OSU_FORGE_DB"
"""Environment variable overriding where the database lives."""

SCHEMA_VERSION = 1
"""The schema this build understands. Raise it and add a migration together."""


class StoreError(RuntimeError):
    """The store could not be opened, or holds something this build cannot read."""


@dataclass(frozen=True, slots=True)
class DeviceRow:
    """One declared device, as stored.

    `attributes` is deliberately open. Everything specific to a kind lives
    there — DPI for a mouse, active area for a tablet — so that a new kind is a
    new value of `kind` rather than a new column that every other kind carries
    as NULL.
    """

    role: str
    """What the device is used for. One active device per role."""

    kind: str
    vendor: str
    model: str
    attributes: dict[str, Any]


# Each migration is (version, statements). Applied in order, inside one
# transaction, and never edited once shipped — a migration that changes after
# someone has run it produces two different databases with the same version
# number, which is worse than any schema mistake it would have fixed.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE devices (
                role       TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,
                vendor     TEXT NOT NULL DEFAULT '',
                model      TEXT NOT NULL DEFAULT '',
                attributes TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
        ),
    ),
)


def default_db_path() -> Path:
    """`%LOCALAPPDATA%\\osu-forge\\osu-forge.db`, or the environment override.

    Beside the journal and the runtime file, outside the osu! install. The
    protection on it is the profile's own ACL — on Windows a file mode is not
    protection, which is measured elsewhere in this project rather than assumed.
    """
    override = os.environ.get(DB_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "osu-forge.db"


class Store:
    """An open database. Use :func:`open_store`, which closes it for you."""

    __slots__ = ("connection", "path")

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self.connection = connection
        self.path = path

    # -- devices ---------------------------------------------------------

    def device(self, role: str) -> DeviceRow | None:
        row = self.connection.execute(
            "SELECT role, kind, vendor, model, attributes FROM devices WHERE role = ?",
            (role,),
        ).fetchone()
        if row is None:
            return None
        try:
            attributes = json.loads(row["attributes"])
        except json.JSONDecodeError as exc:
            raise StoreError(f"device {role!r} has unreadable attributes: {exc}") from exc
        if not isinstance(attributes, dict):
            raise StoreError(f"device {role!r} attributes are not an object")
        return DeviceRow(
            role=row["role"],
            kind=row["kind"],
            vendor=row["vendor"],
            model=row["model"],
            attributes=attributes,
        )

    def put_device(self, device: DeviceRow) -> None:
        """Record the device for its role, replacing whatever was there.

        One device per role rather than a history. A previous mouse is not
        evidence about the current one, and keeping it would invite an analysis
        to average across a hardware change.
        """
        self.connection.execute(
            """
            INSERT INTO devices (role, kind, vendor, model, attributes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(role) DO UPDATE SET
                kind = excluded.kind,
                vendor = excluded.vendor,
                model = excluded.model,
                attributes = excluded.attributes
            """,
            (
                device.role,
                device.kind,
                device.vendor,
                device.model,
                json.dumps(device.attributes, sort_keys=True),
            ),
        )
        self.connection.commit()

    def forget_device(self, role: str) -> bool:
        cursor = self.connection.execute("DELETE FROM devices WHERE role = ?", (role,))
        self.connection.commit()
        return cursor.rowcount > 0

    # -- settings --------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError as exc:
            raise StoreError(f"setting {key!r} is not readable: {exc}") from exc

    def put(self, key: str, value: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value, sort_keys=True)),
        )
        self.connection.commit()

    @property
    def version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])


def _migrate(connection: sqlite3.Connection, path: Path) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise StoreError(
            f"{path}: schema {current} was written by a newer osu-forge, and this build "
            f"understands {SCHEMA_VERSION}. Reading it would silently drop whatever that "
            "build added and then write the file back without it."
        )
    if current == SCHEMA_VERSION:
        return

    with connection:
        for version, statements in _MIGRATIONS:
            if version <= current:
                continue
            for statement in statements:
                connection.execute(statement)
        # Not a parameter: PRAGMA does not take one. The value is an int
        # constant from this module, never anything a caller supplies.
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def open_store(path: Path | None = None) -> Iterator[Store]:
    """Open the database, migrating it forward, and close it afterwards.

    Creates the file and its directory if they are not there — an absent store
    is a first run rather than an error.
    """
    target = path or default_db_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target)
    except (OSError, sqlite3.Error) as exc:
        raise StoreError(f"{target}: {exc}") from exc

    connection.row_factory = sqlite3.Row
    try:
        # Foreign keys are off by default in SQLite and are per-connection, so
        # this has to be set on every open rather than once at creation.
        connection.execute("PRAGMA foreign_keys = ON")
        _migrate(connection, target)
        yield Store(connection, target)
    except sqlite3.Error as exc:
        raise StoreError(f"{target}: {exc}") from exc
    finally:
        connection.close()
