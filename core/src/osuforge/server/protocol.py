"""What the server sends a renderer, and in what shape.

A replay is about forty thousand cursor samples. As JSON that is a megabyte and
a half of decimal text which the browser then has to walk and turn back into
numbers; as a packed buffer it is half a megabyte that becomes a typed array in
one step and can be handed to the GPU without touching it again.

# The layout

Each sample is thirteen bytes, little-endian:

```
offset  type     field
     0  int32    time, milliseconds of map time
     4  float32  cursor x, osu! pixels
     8  float32  cursor y
    12  uint8    keys, the raw four-bit mask
```

Nothing is compressed. The socket is loopback, half a megabyte crosses it in
about a millisecond, and a format a reader can describe in four lines is worth
more here than one they have to decode before they can debug it.

Times are absolute rather than deltas for the same reason: a renderer seeking to
2:14 does a binary search on the array it already has, and delta encoding would
make it walk from the start.

# Map time throughout

Every time in this protocol is map time — what the song is at, not what the
clock is. Under Double Time the two differ by half, and map time is the one that
matches the beatmap, the timestamps in the analysis, and what a player would
seek to. The rate is sent once in the header for anything that needs real
seconds.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

import osu_forge_diffcalc as diffcalc

from osuforge.replay.model import ReplayFrame
from osuforge.replay.simulate import Grade, Simulation

__all__ = [
    "FRAME_STRUCT",
    "SAMPLE_BYTES",
    "SCHEMA_VERSION",
    "ReplayPayload",
    "encode_frames",
    "objects_payload",
]

SCHEMA_VERSION = 1

FRAME_STRUCT = struct.Struct("<iffB")
SAMPLE_BYTES = FRAME_STRUCT.size
assert SAMPLE_BYTES == 13


def encode_frames(frames: list[ReplayFrame]) -> bytes:
    """Pack cursor samples into the buffer described in the module docstring."""
    buffer = bytearray(SAMPLE_BYTES * len(frames))
    for index, frame in enumerate(frames):
        FRAME_STRUCT.pack_into(
            buffer, index * SAMPLE_BYTES, frame.time, frame.x, frame.y, int(frame.keys) & 0xFF
        )
    return bytes(buffer)


def objects_payload(beatmap: diffcalc.Beatmap) -> list[dict[str, Any]]:
    """Hit objects, as the renderer needs them.

    Positions are the stacked ones, because those are where the game drew them
    and a replay drawn against the file positions would show the cursor missing
    things it hit.

    Slider paths are **not** here yet. The geometry lives in the Rust layer and
    is not exposed through the bindings, so a slider arrives as its head and its
    duration and will draw as a circle that lasts. That is a gap rather than a
    decision, and it is better stated than quietly rendered wrong.
    """
    kinds = {
        diffcalc.ObjectKind.Circle: "circle",
        diffcalc.ObjectKind.Slider: "slider",
        diffcalc.ObjectKind.Spinner: "spinner",
    }
    return [
        {
            "t": obj.time,
            "end": obj.end_time,
            "x": round(obj.x, 2),
            "y": round(obj.y, 2),
            "kind": kinds[obj.kind],
            "combo": obj.new_combo,
        }
        for obj in beatmap.objects
    ]


@dataclass(frozen=True, slots=True)
class ReplayPayload:
    """Everything a renderer needs for one replay, ready to serialise."""

    header: dict[str, Any]
    frames: bytes

    @classmethod
    def build(
        cls,
        *,
        replay_name: str,
        beatmap: diffcalc.Beatmap,
        simulation: Simulation,
        rate: float,
    ) -> ReplayPayload:
        counts = simulation.counts()
        header = {
            "schema_version": SCHEMA_VERSION,
            "replay": replay_name,
            "rate": rate,
            "sample_bytes": SAMPLE_BYTES,
            "sample_count": len(simulation.frames.frames),
            "beatmap": {
                "artist": beatmap.artist,
                "title": beatmap.title,
                "version": beatmap.version,
                "radius": beatmap.radius,
                "preempt": beatmap.preempt,
                "circle_size": beatmap.circle_size,
                "approach_rate": beatmap.approach_rate,
                "overall_difficulty": beatmap.overall_difficulty,
                "background": beatmap.background,
            },
            "windows": simulation.windows,
            "counts": {
                "300": counts[Grade.THREE_HUNDRED],
                "100": counts[Grade.HUNDRED],
                "50": counts[Grade.FIFTY],
                "miss": counts[Grade.MISS],
            },
            "objects": objects_payload(beatmap),
            # Judgement per object, aligned with `objects` by position, so the
            # renderer can colour what happened without re-deriving it.
            "judgements": [
                {
                    "grade": int(hit.grade),
                    "error": hit.error,
                    "aim": None if hit.aim_error is None else round(hit.aim_error, 3),
                }
                for hit in simulation.hits
            ],
        }
        return cls(header=header, frames=encode_frames(simulation.frames.frames))
