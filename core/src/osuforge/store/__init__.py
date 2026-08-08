"""Where osu-forge keeps what the player told it.

One SQLite file, in osu-forge's own directory, holding the things no osu! file
records. It exists instead of a JSON blob for one reason: the shape will change.
Tablets are a device kind that does not exist yet, mice have vendors and models
worth recording, and a second vendor's sensor presets should be a row rather
than a code change. A JSON file with one hardcoded shape survives none of that
without either a breaking change or a pile of optional keys nobody can audit.

Nothing in here writes to anything osu! owns.
"""

from osuforge.store.db import (
    SCHEMA_VERSION,
    Store,
    StoreError,
    default_db_path,
    open_store,
)

__all__ = [
    "SCHEMA_VERSION",
    "Store",
    "StoreError",
    "default_db_path",
    "open_store",
]
