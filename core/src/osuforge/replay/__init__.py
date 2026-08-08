"""Reading osu! replays.

Read-only. Replay files are opened in binary mode and never written.

A replay's filename is `<beatmapMD5>-<ticks>.osr`, so a replay can be matched to
a beatmap without opening either — see :func:`beatmap_hash_from_filename`.
"""

from __future__ import annotations

import re
from pathlib import Path

from osuforge.replay.model import (
    Judgements,
    Keys,
    Mods,
    Replay,
    ReplayFrame,
    Ruleset,
)
from osuforge.replay.parse import ReplayParseError, parse, parse_path

__all__ = [
    "Judgements",
    "Keys",
    "Mods",
    "Replay",
    "ReplayFrame",
    "ReplayParseError",
    "Ruleset",
    "beatmap_hash_from_filename",
    "find_replay_dir",
    "parse",
    "parse_path",
]

_REPLAY_NAME = re.compile(r"^([0-9a-f]{32})-(\d+)\.osr$", re.IGNORECASE)


def beatmap_hash_from_filename(path: Path) -> str | None:
    """The beatmap MD5 encoded in a replay's filename, if it follows the
    convention osu! uses for local replays.

    Cheap enough to index a whole folder without decompressing anything.
    Returns ``None`` for a file named some other way rather than guessing.
    """
    match = _REPLAY_NAME.match(path.name)
    return match.group(1).lower() if match else None


def find_replay_dir(install_dir: Path) -> Path:
    """osu!stable keeps local replays in ``Data/r``."""
    return install_dir / "Data" / "r"
