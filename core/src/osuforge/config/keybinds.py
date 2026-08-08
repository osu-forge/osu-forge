"""Keybindings, and when two of them actually conflict.

The naive check — invert the binding table and report any value bound twice —
produces false positives on a perfectly healthy config, because osu! binds
different rulesets and different screens independently. On a real config it
reports four collisions, of which exactly one is real.

So each bindable key declares the **activation scopes** where it is live, and

    conflict  iff  bindings are equal
              and  scopes intersect
              and  neither is unbound

Validated against a real config, this gives:

===========================================  ==================  ==========
pair                                         scopes              verdict
===========================================  ==================  ==========
``keySkip`` / ``keyQuickRetry`` = Space      both gameplay       conflict
``keyOsuSmoke`` / ``keyFruitsDash`` = Shift  osu vs fruits       fine
``keyOsuLeft`` / ``keySuddenDeath`` = S      osu vs mod select   fine
``keyTaikoOuterLeft`` / ``keyJumpToBegin``   taiko vs editor     fine
===========================================  ==================  ==========

The three true-negatives are the point. A conflict detector that cries wolf on
a healthy config gets ignored, and then it is worth nothing when it is right.

Keys whose scope is not known are **excluded from conflict detection** rather
than guessed at. A missed conflict is a gap; an invented one destroys trust in
every other finding the tool produces.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from osuforge.config.parser import OsuConfig

__all__ = [
    "UNBOUND",
    "Binding",
    "BindingConflict",
    "KeyScope",
    "find_conflicts",
    "read_bindings",
    "scopes_for",
]

UNBOUND: Final = "None"
"""osu! spells an unbound key as the literal string ``None``. Two unbound keys
are not a conflict, and this trips up anyone who diffs the raw values."""


class KeyScope(StrEnum):
    """A context in which a binding is live."""

    PLAY_OSU = "play:osu"
    PLAY_TAIKO = "play:taiko"
    PLAY_FRUITS = "play:fruits"
    PLAY_MANIA = "play:mania"
    EDITOR = "editor"
    MOD_SELECT = "modselect"


_ALL_PLAY: Final = frozenset(
    {KeyScope.PLAY_OSU, KeyScope.PLAY_TAIKO, KeyScope.PLAY_FRUITS, KeyScope.PLAY_MANIA}
)
_EVERYWHERE: Final = frozenset(KeyScope)

# Exact key names, from a real osu!.<user>.cfg. Anything not here and not
# matched by a pattern below has unknown scope and is skipped.
_EXACT_SCOPES: Final[dict[str, frozenset[KeyScope]]] = {
    # Live in every context, gameplay included.
    "keyScreenshot": _EVERYWHERE,
    "keyBossKey": _EVERYWHERE,
    "keyToggleFrameLimiter": _EVERYWHERE,
    "keyIncreaseVolume": _EVERYWHERE,
    "keyDecreaseVolume": _EVERYWHERE,
    # Live during gameplay in every ruleset.
    "keySkip": _ALL_PLAY,
    "keyQuickRetry": _ALL_PLAY,
    "keyPause": _ALL_PLAY,
    "keyToggleChat": _ALL_PLAY,
    "keyToggleScoreboard": _ALL_PLAY,
    "keyIncreaseAudioOffset": _ALL_PLAY,
    "keyDecreaseAudioOffset": _ALL_PLAY,
    "keyDisableMouseButtons": _ALL_PLAY,
    # Per-ruleset gameplay.
    "keyOsuLeft": frozenset({KeyScope.PLAY_OSU}),
    "keyOsuRight": frozenset({KeyScope.PLAY_OSU}),
    "keyOsuSmoke": frozenset({KeyScope.PLAY_OSU}),
    "keyTaikoInnerLeft": frozenset({KeyScope.PLAY_TAIKO}),
    "keyTaikoInnerRight": frozenset({KeyScope.PLAY_TAIKO}),
    "keyTaikoOuterLeft": frozenset({KeyScope.PLAY_TAIKO}),
    "keyTaikoOuterRight": frozenset({KeyScope.PLAY_TAIKO}),
    "keyFruitsLeft": frozenset({KeyScope.PLAY_FRUITS}),
    "keyFruitsRight": frozenset({KeyScope.PLAY_FRUITS}),
    "keyFruitsDash": frozenset({KeyScope.PLAY_FRUITS}),
}

# Families. Mania bindings are per-key-count (keyMania4K1, keyMania7K3, ...) and
# are frequently absent entirely — osu! only writes them once mania is
# configured, so a parser must treat them as optional rather than defaulted.
_PATTERN_SCOPES: Final[tuple[tuple[re.Pattern[str], frozenset[KeyScope]], ...]] = (
    (re.compile(r"^keyMania\d+K\d+$"), frozenset({KeyScope.PLAY_MANIA})),
    (
        re.compile(
            r"^key("
            r"Easy|NoFail|HalfTime|HardRock|SuddenDeath|DoubleTime|Hidden|Flashlight"
            r"|Relax|Autopilot|SpunOut|Auto|Cinema|Perfect|Nightcore|Target|ScoreV2"
            r"|Random|Coop|Key\d"
            r")$"
        ),
        frozenset({KeyScope.MOD_SELECT}),
    ),
    (
        re.compile(
            r"^key("
            r"Nudge\w*|JumpTo\w*|SeekTo\w*|Select\w*Tool|Grid\w*|Timing\w*"
            r"|Editor\w*|Bookmark\w*|Clone|Reverse"
            r")$"
        ),
        frozenset({KeyScope.EDITOR}),
    ),
)


def scopes_for(key: str) -> frozenset[KeyScope] | None:
    """Scopes a binding is live in, or ``None`` when the key is not recognised.

    ``None`` means "do not reason about this key", not "no scopes". Callers must
    exclude it from conflict detection rather than treating it as universal.
    """
    known = _EXACT_SCOPES.get(key)
    if known is not None:
        return known
    for pattern, scopes in _PATTERN_SCOPES:
        if pattern.match(key):
            return scopes
    return None


@dataclass(frozen=True, slots=True)
class Binding:
    """One ``key* = Value`` entry with its resolved scopes."""

    key: str
    value: str
    scopes: frozenset[KeyScope] | None
    lineno: int

    @property
    def bound(self) -> bool:
        return self.value != UNBOUND and bool(self.value.strip())

    @property
    def known(self) -> bool:
        return self.scopes is not None

    def overlaps(self, other: Binding) -> frozenset[KeyScope]:
        if self.scopes is None or other.scopes is None:
            return frozenset()
        return self.scopes & other.scopes


@dataclass(frozen=True, slots=True)
class BindingConflict:
    """Two bindings that are live at the same time and bound to the same key."""

    value: str
    bindings: tuple[Binding, ...]
    scopes: frozenset[KeyScope]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(binding.key for binding in self.bindings)

    def describe(self) -> str:
        scope_list = ", ".join(sorted(self.scopes))
        return f"{' and '.join(self.keys)} are both bound to {self.value} (live in: {scope_list})"


def read_bindings(config: OsuConfig) -> list[Binding]:
    """Every ``key*`` entry in the config, with resolved scopes."""
    bindings: list[Binding] = []
    for key, entry in config.entries.items():
        if not key.startswith("key"):
            continue
        value = entry.value
        if not isinstance(value, str):
            # A key* entry should never be redacted, but if the policy ever
            # matched one, reasoning about its value would be meaningless.
            continue
        bindings.append(Binding(key=key, value=value, scopes=scopes_for(key), lineno=entry.lineno))
    return bindings


def _pairs(items: list[Binding]) -> Iterator[tuple[Binding, Binding]]:
    for i, first in enumerate(items):
        for second in items[i + 1 :]:
            yield first, second


def find_conflicts(config: OsuConfig) -> list[BindingConflict]:
    """Bindings that are genuinely live at the same time on the same key.

    Grouped by value, so three keys sharing one binding are reported once rather
    than as three pairs.
    """
    candidates = [b for b in read_bindings(config) if b.bound and b.known]

    by_value: dict[str, list[Binding]] = {}
    for binding in candidates:
        by_value.setdefault(binding.value, []).append(binding)

    conflicts: list[BindingConflict] = []
    for value, group in sorted(by_value.items()):
        if len(group) < 2:
            continue

        # Only report keys that actually overlap with at least one other key in
        # the group. Without this, `keyOsuSmoke` and `keyFruitsDash` sharing
        # LeftShift with a third genuinely-conflicting key would drag both into
        # the report even though neither conflicts with the other.
        overlapping: dict[str, Binding] = {}
        shared: set[KeyScope] = set()
        for first, second in _pairs(group):
            intersection = first.overlaps(second)
            if intersection:
                overlapping[first.key] = first
                overlapping[second.key] = second
                shared |= intersection

        if len(overlapping) < 2:
            continue

        conflicts.append(
            BindingConflict(
                value=value,
                bindings=tuple(sorted(overlapping.values(), key=lambda b: b.lineno)),
                scopes=frozenset(shared),
            )
        )

    return conflicts
