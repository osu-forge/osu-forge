"""`.osr` replay parser.

Written from the osu! file-format documentation. The layout is a fact about the
game; see ``docs/licensing.md``.

The header is little-endian primitives plus a length-prefixed string encoding.
The frame stream is LZMA-compressed ASCII, `time|x|y|keys` separated by commas,
where `time` is a **delta** from the previous frame rather than an absolute.

Three quirks are structural, not corruption, and every real replay has them:

- The first two frames are markers at ``(256, -500)`` — a position no cursor can
  occupy. They carry no input.
- The third frame usually has a large **negative** delta. It records the lead-in
  or skip, and it is an absolute start offset rather than something to add.
- The last frame is ``-12345|0|0|<seed>``: the RNG seed, not a sample. Left in,
  it lands 12 seconds before the start and drags any timing statistic with it.

Handling those is :mod:`osuforge.replay.frames`. This module parses; it does not
interpret.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

from osuforge.replay.model import Judgements, Keys, Mods, Replay, ReplayFrame, Ruleset

__all__ = ["ReplayParseError", "parse", "parse_path"]

# .NET DateTime ticks are 100 ns since 0001-01-01. Python's datetime starts at
# year 1 too, so the conversion is one subtraction rather than an epoch table.
_TICKS_PER_SECOND = 10_000_000
_DOTNET_EPOCH = datetime(1, 1, 1, tzinfo=UTC)

_SEED_FRAME_DELTA = -12345


class ReplayParseError(ValueError):
    """A replay could not be read.

    Carries the byte offset, because "corrupt replay" is not actionable and a
    zero-byte file is a different problem from a truncated one.
    """

    def __init__(self, message: str, *, offset: int | None = None) -> None:
        self.offset = offset
        super().__init__(message if offset is None else f"{message} (at byte {offset})")


class _Reader:
    """Cursor over the header's primitives."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _take(self, count: int) -> bytes:
        end = self.pos + count
        if end > len(self.data):
            raise ReplayParseError(
                f"wanted {count} bytes but only {len(self.data) - self.pos} remain",
                offset=self.pos,
            )
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def byte(self) -> int:
        return self._take(1)[0]

    def short(self) -> int:
        return int(struct.unpack("<H", self._take(2))[0])

    def int32(self) -> int:
        return int(struct.unpack("<i", self._take(4))[0])

    def int64(self) -> int:
        return int(struct.unpack("<q", self._take(8))[0])

    def uleb128(self) -> int:
        value = 0
        shift = 0
        while True:
            byte = self.byte()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 63:
                raise ReplayParseError("ULEB128 length is implausibly large", offset=self.pos)

    def string(self) -> str:
        """A length-prefixed string, or empty.

        The format marks presence with a leading byte: 0x00 means absent, 0x0b
        means a ULEB128 length follows. Anything else means we have lost
        alignment, and continuing from there would produce plausible-looking
        garbage rather than an error.
        """
        marker = self.byte()
        if marker == 0x00:
            return ""
        if marker != 0x0B:
            raise ReplayParseError(
                f"expected a string marker (0x00 or 0x0b), found 0x{marker:02x}",
                offset=self.pos - 1,
            )
        length = self.uleb128()
        return self._take(length).decode("utf-8", errors="replace")


def parse(data: bytes, *, source: Path | None = None) -> Replay:
    """Parse replay bytes."""
    if not data:
        raise ReplayParseError(f"file is empty{f' ({source.name})' if source else ''}", offset=0)

    reader = _Reader(data)

    ruleset_id = reader.byte()
    try:
        ruleset = Ruleset(ruleset_id)
    except ValueError as exc:
        raise ReplayParseError(f"unknown ruleset id {ruleset_id}", offset=0) from exc

    game_version = reader.int32()
    beatmap_hash = reader.string()
    player_name = reader.string()
    replay_hash = reader.string()

    judgements = Judgements(
        count_300=reader.short(),
        count_100=reader.short(),
        count_50=reader.short(),
        count_geki=reader.short(),
        count_katu=reader.short(),
        count_miss=reader.short(),
    )

    score = reader.int32()
    max_combo = reader.short()
    perfect_combo = reader.byte() != 0
    mods = Mods(reader.int32())
    life_bar = reader.string()
    timestamp = _from_ticks(reader.int64())

    compressed_length = reader.int32()
    compressed = reader._take(compressed_length) if compressed_length > 0 else b""

    # Present since 2019; absent in older replays, where the header simply ends.
    online_score_id = reader.int64() if reader.pos + 8 <= len(data) else 0

    frames, seed = _parse_frames(compressed)

    return Replay(
        ruleset=ruleset,
        game_version=game_version,
        beatmap_hash=beatmap_hash,
        player_name=player_name,
        replay_hash=replay_hash,
        judgements=judgements,
        score=score,
        max_combo=max_combo,
        perfect_combo=perfect_combo,
        mods=mods,
        life_bar=life_bar,
        timestamp=timestamp,
        online_score_id=online_score_id,
        frames=frames,
        seed=seed,
    )


def parse_path(path: Path) -> Replay:
    """Read and parse a replay file. Opened read-only, never written."""
    return parse(path.read_bytes(), source=path)


def _from_ticks(ticks: int) -> datetime:
    return _DOTNET_EPOCH + timedelta(seconds=ticks / _TICKS_PER_SECOND)


def _parse_frames(compressed: bytes) -> tuple[list[ReplayFrame], int | None]:
    """Decompress and parse the frame stream.

    Returns the frames with absolute times, and the RNG seed if the trailing
    seed frame was present. The seed is separated here rather than left in the
    list: its ``-12345`` delta would otherwise be summed into the running time
    and place a phantom frame twelve seconds before the map starts.
    """
    if not compressed:
        return [], None

    try:
        raw = lzma.decompress(compressed)
    except lzma.LZMAError as exc:
        raise ReplayParseError(f"frame data would not decompress: {exc}") from exc

    frames: list[ReplayFrame] = []
    seed: int | None = None
    running_time = 0

    for entry in raw.decode("ascii", errors="replace").split(","):
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) != 4:
            continue

        try:
            delta = int(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            keys_raw = int(parts[3])
        except ValueError:
            # A malformed frame is skipped rather than failing the replay. The
            # rest of the stream is still usable, and the count reaching the
            # validation gate is what decides whether it can be trusted.
            continue

        if delta == _SEED_FRAME_DELTA:
            seed = keys_raw
            continue

        running_time += delta
        frames.append(
            ReplayFrame(
                time=running_time,
                delta=delta,
                x=x,
                y=y,
                # Bits above SMOKE are not defined for osu!standard; masking
                # keeps an unexpected value from becoming a spurious key press.
                keys=Keys(keys_raw & 0b11111),
            )
        )

    return frames, seed
