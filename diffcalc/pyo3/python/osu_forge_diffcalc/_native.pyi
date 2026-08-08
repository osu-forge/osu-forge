"""Type stubs for the compiled module.

Hand-written, and therefore capable of drifting from the Rust. What keeps it
honest is that `osuforge` is checked under `mypy --strict` against these names:
a signature that no longer matches produces a type error at the first call site
rather than an AttributeError at runtime.
"""

from pathlib import Path
from typing import ClassVar, final

MODS: dict[str, int]
"""Mod bit values as the Rust side reads them. `osuforge.replay.model.Mods`
keeps its own copy for the `.osr` header, and a test asserts the two agree."""

STACK_DISTANCE: float
VERSION: str

class BeatmapError(Exception):
    """A `.osu` file could not be read."""

@final
class ObjectKind:
    Circle: ClassVar[ObjectKind]
    Slider: ClassVar[ObjectKind]
    Spinner: ClassVar[ObjectKind]

    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...
    def __int__(self) -> int: ...

@final
class HitObject:
    @property
    def time(self) -> int:
        """Milliseconds of map time."""

    @property
    def end_time(self) -> float:
        """When the object stops accepting input; equal to `time` for a circle."""

    @property
    def x(self) -> float:
        """Where the object is drawn and judged, with stacking applied."""

    @property
    def y(self) -> float: ...
    @property
    def raw_x(self) -> float:
        """Position as written in the file, before stacking."""

    @property
    def raw_y(self) -> float: ...
    @property
    def kind(self) -> ObjectKind: ...
    @property
    def new_combo(self) -> bool: ...
    @property
    def stack_height(self) -> int: ...

@final
class Beatmap:
    @staticmethod
    def from_bytes(data: bytes) -> Beatmap: ...
    @staticmethod
    def from_file(path: str | Path) -> Beatmap: ...
    def with_mods(self, mods: int) -> Beatmap:
        """The beatmap as played under `mods`, restacked.

        Only Hard Rock and Easy change anything. Double Time and Half Time
        change the clock rather than the beatmap.
        """

    @property
    def objects(self) -> list[HitObject]: ...
    @property
    def format_version(self) -> int: ...
    @property
    def stacked(self) -> bool:
        """False for files older than format v6, whose stacking algorithm is not
        implemented; positions on those are the raw file positions."""

    @property
    def stack_leniency(self) -> float: ...
    @property
    def circle_size(self) -> float: ...
    @property
    def approach_rate(self) -> float: ...
    @property
    def overall_difficulty(self) -> float: ...
    @property
    def hp_drain_rate(self) -> float: ...
    @property
    def slider_multiplier(self) -> float: ...
    @property
    def slider_tick_rate(self) -> float: ...
    @property
    def radius(self) -> float:
        """Hit-object radius in osu! pixels."""

    @property
    def preempt(self) -> float: ...
    @property
    def hit_window_300(self) -> float:
        """Half-width in milliseconds of map time, unrounded."""

    @property
    def hit_window_100(self) -> float: ...
    @property
    def hit_window_50(self) -> float: ...
    @property
    def title(self) -> str: ...
    @property
    def artist(self) -> str: ...
    @property
    def creator(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def beatmap_id(self) -> int | None: ...
    @property
    def beatmap_set_id(self) -> int | None: ...
