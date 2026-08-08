"""Replay data model.

Field names follow the `.osr` format's own naming so each can be checked
against the format documentation without translation. Two places where the
format's name is actively misleading are renamed, and both say why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, IntFlag

__all__ = ["Judgements", "Keys", "Mods", "Replay", "ReplayFrame", "Ruleset"]


class Ruleset(IntEnum):
    OSU = 0
    TAIKO = 1
    CATCH = 2
    MANIA = 3


class Mods(IntFlag):
    """Mod bitmask from the replay header."""

    NONE = 0
    NO_FAIL = 1 << 0
    EASY = 1 << 1
    TOUCH_DEVICE = 1 << 2
    HIDDEN = 1 << 3
    HARD_ROCK = 1 << 4
    SUDDEN_DEATH = 1 << 5
    DOUBLE_TIME = 1 << 6
    RELAX = 1 << 7
    HALF_TIME = 1 << 8
    NIGHTCORE = 1 << 9
    FLASHLIGHT = 1 << 10
    AUTOPLAY = 1 << 11
    SPUN_OUT = 1 << 12
    AUTOPILOT = 1 << 13
    PERFECT = 1 << 14
    KEY4 = 1 << 15
    KEY5 = 1 << 16
    KEY6 = 1 << 17
    KEY7 = 1 << 18
    KEY8 = 1 << 19
    FADE_IN = 1 << 20
    RANDOM = 1 << 21
    CINEMA = 1 << 22
    # osu!'s own name for this is Target Practice. It was TARGET here until the
    # cross-check against the Rust mod table objected, which is what that test
    # is for.
    TARGET_PRACTICE = 1 << 23
    KEY9 = 1 << 24
    KEY_COOP = 1 << 25
    KEY1 = 1 << 26
    KEY3 = 1 << 27
    KEY2 = 1 << 28
    SCORE_V2 = 1 << 29
    MIRROR = 1 << 30

    @property
    def rate(self) -> float:
        """Playback rate. Nightcore always sets Double Time as well."""
        if self & Mods.DOUBLE_TIME:
            return 1.5
        if self & Mods.HALF_TIME:
            return 0.75
        return 1.0

    @property
    def not_human(self) -> bool:
        """Whether the inputs were assisted.

        Relax, Autopilot and Autoplay replays record something other than a
        person's timing, so including them in a hit-error distribution would
        pull the mean toward whatever the assist does. Excluded rather than
        weighted.
        """
        return bool(self & (Mods.RELAX | Mods.AUTOPILOT | Mods.AUTOPLAY | Mods.CINEMA))

    @property
    def changes_difficulty(self) -> bool:
        return bool(self & (Mods.EASY | Mods.HARD_ROCK))

    def acronyms(self) -> list[str]:
        return [m.name for m in Mods if m.value and m.value & self.value and m.name]


class Keys(IntFlag):
    """Key state bitmask on a replay frame.

    ``K1`` always sets ``M1`` too, and ``K2`` always sets ``M2`` — so a keyboard
    press reads as ``5``, not ``4``. Two consequences that are easy to miss:

    - For judgement, mask to ``M1 | M2``. That collapses the pairs and dedupes
      automatically, which is why :attr:`judgement_mask` exists.
    - To tell a real mouse click from a keyboard press, check the *unmasked*
      bits: a genuine M1 click is ``keys & M1`` with ``K1`` clear. Edge-detecting
      only on K1/K2 silently drops every mouse click.
    """

    NONE = 0
    M1 = 1 << 0
    M2 = 1 << 1
    K1 = 1 << 2
    K2 = 1 << 3
    SMOKE = 1 << 4

    @property
    def judgement_mask(self) -> int:
        """Just the two bits that decide whether a press happened."""
        return self.value & (Keys.M1 | Keys.M2)

    @property
    def mouse_only(self) -> bool:
        """A real mouse click: the mouse bit set without its keyboard partner."""
        m1 = bool(self & Keys.M1) and not self & Keys.K1
        m2 = bool(self & Keys.M2) and not self & Keys.K2
        return m1 or m2


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    """One recorded input sample.

    ``time`` is absolute, reconstructed from the file's deltas.

    ``delta`` is kept per frame on purpose. osu!stable samples input once per
    frame, so a press is recorded at the first frame at or after it actually
    happened — which makes the delta the measurement uncertainty on this
    frame's timestamp. Every downstream metric needs it, and recovering it later
    from neighbouring frames is lossy once frames get filtered.
    """

    time: int
    delta: int
    x: float
    y: float
    keys: Keys


@dataclass(frozen=True, slots=True)
class Judgements:
    """Hit counts as recorded in the header.

    This is ground truth. A simulator that replays the same inputs against the
    same beatmap must reproduce these counts, and if it does not then its hit
    errors describe something other than what happened.
    """

    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int

    @property
    def total(self) -> int:
        return self.count_300 + self.count_100 + self.count_50 + self.count_miss

    @property
    def accuracy(self) -> float:
        """osu!standard accuracy, 0 to 1."""
        judged = self.total
        if judged == 0:
            return 0.0
        weighted = self.count_300 * 300 + self.count_100 * 100 + self.count_50 * 50
        return weighted / (judged * 300)


@dataclass(frozen=True, slots=True)
class Replay:
    ruleset: Ruleset
    game_version: int
    """Client build as an integer date, e.g. 20260711."""

    beatmap_hash: str
    """MD5 of the `.osu` file. Also the first half of the replay's own filename,
    which is how a replay is matched to a beatmap without opening either."""

    player_name: str
    replay_hash: str
    judgements: Judgements
    score: int
    max_combo: int
    perfect_combo: bool
    mods: Mods
    life_bar: str
    timestamp: datetime
    online_score_id: int
    frames: list[ReplayFrame] = field(default_factory=list)

    seed: int | None = None
    """RNG seed from the trailing `-12345` frame. Not an input sample."""

    @property
    def rate(self) -> float:
        return self.mods.rate

    @property
    def usable_for_timing(self) -> bool:
        """Whether this replay's frames describe a person's timing.

        False for assisted play and for anything that is not osu!standard.
        """
        return self.ruleset is Ruleset.OSU and not self.mods.not_human
