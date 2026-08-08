"""Build `.osr` bytes programmatically.

Written rather than checked in, for the same reason the config fixtures are: no
real replay enters the repository, and a constructed file has *known* ground
truth. A test that feeds in a deliberate ±5 ms distribution and checks the mean
that comes back validates the parser; one that reads a real replay and checks it
does not crash only smoke-tests it.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime

__all__ = ["build_replay", "encode_frames", "encode_string"]

_TICKS_PER_SECOND = 10_000_000
_DOTNET_EPOCH = datetime(1, 1, 1, tzinfo=UTC)

# LZMA1 with the filter properties osu! writes. The frame block has no
# end-of-stream marker and no container header, so the decompressor has to be
# told the format explicitly on both sides.
_FILTERS = [{"id": lzma.FILTER_LZMA1, "preset": 6}]


def encode_string(value: str) -> bytes:
    """The format's length-prefixed string: 0x00 for absent, else 0x0b + ULEB128."""
    if not value:
        return b"\x00"
    raw = value.encode("utf-8")
    length = bytearray()
    size = len(raw)
    while True:
        byte = size & 0x7F
        size >>= 7
        if size:
            length.append(byte | 0x80)
        else:
            length.append(byte)
            break
    return b"\x0b" + bytes(length) + raw


def encode_frames(frames: list[tuple[int, float, float, int]], *, seed: int | None = 1234) -> bytes:
    """Compress a frame list. Each entry is (delta, x, y, keys).

    A trailing seed frame is appended by default because every real replay has
    one, so a test that omits it would be testing a file osu! never writes.
    """
    parts = [f"{delta}|{x}|{y}|{keys}" for delta, x, y, keys in frames]
    if seed is not None:
        parts.append(f"-12345|0|0|{seed}")
    text = ",".join(parts) + ","
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_ALONE, filters=_FILTERS)
    return compressor.compress(text.encode("ascii")) + compressor.flush()


def build_replay(
    *,
    ruleset: int = 0,
    game_version: int = 20260711,
    beatmap_hash: str = "0" * 32,
    player_name: str = "tester",
    replay_hash: str = "f" * 32,
    count_300: int = 100,
    count_100: int = 10,
    count_50: int = 1,
    count_geki: int = 20,
    count_katu: int = 5,
    count_miss: int = 2,
    score: int = 1_234_567,
    max_combo: int = 111,
    perfect_combo: bool = False,
    mods: int = 0,
    life_bar: str = "0|1,1000|0.5",
    timestamp: datetime | None = None,
    frames: list[tuple[int, float, float, int]] | None = None,
    seed: int | None = 1234,
    online_score_id: int = 0,
    include_online_score_id: bool = True,
) -> bytes:
    """Assemble a complete replay file."""
    when = timestamp or datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    ticks = int((when - _DOTNET_EPOCH).total_seconds() * _TICKS_PER_SECOND)

    compressed = encode_frames(frames or [], seed=seed) if frames is not None else b""

    out = bytearray()
    out.append(ruleset)
    out += struct.pack("<i", game_version)
    out += encode_string(beatmap_hash)
    out += encode_string(player_name)
    out += encode_string(replay_hash)
    out += struct.pack(
        "<HHHHHH", count_300, count_100, count_50, count_geki, count_katu, count_miss
    )
    out += struct.pack("<i", score)
    out += struct.pack("<H", max_combo)
    out.append(1 if perfect_combo else 0)
    out += struct.pack("<i", mods)
    out += encode_string(life_bar)
    out += struct.pack("<q", ticks)
    out += struct.pack("<i", len(compressed))
    out += compressed
    if include_online_score_id:
        out += struct.pack("<q", online_score_id)
    return bytes(out)
