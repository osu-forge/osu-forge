"""Beatmap geometry for osu!std, compiled from the Rust implementation.

This package exists so that the replay hit simulator and difficulty calculation
read beatmaps through one parser. The alternative — a second parser written in
Python — would eventually disagree with the first about a slider duration or a
stack height, and the disagreement would show up as hit errors that look like a
player's timing rather than like a bug.

Everything here is a thin re-export of the compiled `_native` module. The
indirection is what gives the type stubs a place to live: `osuforge` is checked
under ``mypy --strict``, and an untyped import right where beatmap geometry
enters Python would be the worst possible place to lose types.
"""

from ._native import (
    MODS,
    STACK_DISTANCE,
    VERSION,
    Beatmap,
    BeatmapError,
    HitObject,
    ObjectKind,
)

__all__ = [
    "MODS",
    "STACK_DISTANCE",
    "VERSION",
    "Beatmap",
    "BeatmapError",
    "HitObject",
    "ObjectKind",
]
