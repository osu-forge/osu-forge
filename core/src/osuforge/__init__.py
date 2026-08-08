"""osu-forge — extensible analysis and tuning platform for osu!stable.

osu-forge never writes to osu! memory, never synthesizes input, and never modifies
game files. Settings are recommended as a diff; the user applies them in-game.
See ``docs/architecture.md``.
"""

from __future__ import annotations

from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    SampleSize,
    Severity,
    Subsystem,
)

__version__ = "0.1.0"

__all__ = [
    "Basis",
    "Category",
    "Confidence",
    "Evidence",
    "Finding",
    "SampleSize",
    "Severity",
    "Subsystem",
    "__version__",
]
