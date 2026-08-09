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

# Slider bodies are a second buffer

For the same reason, and more sharply. A map's slider paths are on the order of
a hundred thousand points; as JSON that is most of a megabyte of decimal text
to describe geometry the GPU wants as a flat float array anyway. They travel as
one `float32` blob of interleaved `x, y`, and each slider in the header carries
the offset and length of its own run.

Two buffers rather than one because they change on different schedules: the
frames belong to a replay and the paths belong to a beatmap, so a client
comparing two attempts at the same map fetches the paths once.

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
    "PATH_POINT_BYTES",
    "SAMPLE_BYTES",
    "SCHEMA_VERSION",
    "ReplayPayload",
    "encode_frames",
    "encode_paths",
    "objects_payload",
]

SCHEMA_VERSION = 2
"""Raised from 1 when slider paths were added.

The header gained `paths` and each slider object gained `p` and `slides`, so a
client written against 1 would draw sliders as circles rather than fail — which
is exactly the case a version number has to make visible.
"""

FRAME_STRUCT = struct.Struct("<iffB")
SAMPLE_BYTES = FRAME_STRUCT.size
assert SAMPLE_BYTES == 13

PATH_POINT_BYTES = 8
"""One `float32` pair. The unit of the slider path buffer."""


def encode_frames(frames: list[ReplayFrame]) -> bytes:
    """Pack cursor samples into the buffer described in the module docstring."""
    buffer = bytearray(SAMPLE_BYTES * len(frames))
    for index, frame in enumerate(frames):
        FRAME_STRUCT.pack_into(
            buffer, index * SAMPLE_BYTES, frame.time, frame.x, frame.y, int(frame.keys) & 0xFF
        )
    return bytes(buffer)


def encode_paths(beatmap: diffcalc.Beatmap) -> tuple[bytes, list[tuple[int, int]]]:
    """Pack every slider body into one buffer, and say where each one sits.

    Returns the blob and, per object in beatmap order, `(offset, count)` in
    points — `(0, 0)` for anything that is not a slider. Positions come from the
    bindings already trimmed to the declared length and already carrying the
    stack offset, so nothing here reinterprets the geometry.
    """
    blob = bytearray()
    spans: list[tuple[int, int]] = []
    points = 0
    for obj in beatmap.objects:
        path = obj.path
        if not path:
            spans.append((0, 0))
            continue
        count = len(path) // 2
        blob.extend(struct.pack(f"<{len(path)}f", *path))
        spans.append((points, count))
        points += count
    return bytes(blob), spans


def objects_payload(beatmap: diffcalc.Beatmap) -> list[dict[str, Any]]:
    """Hit objects, as the renderer needs them.

    Positions are the stacked ones, because those are where the game drew them
    and a replay drawn against the file positions would show the cursor missing
    things it hit.

    A slider carries `p` — its `[offset, count]` in the path buffer — and
    `slides`. Both are needed to draw it: the body comes from the buffer, and
    the slide count decides which end the ball is at when the next object
    begins, so a renderer without it puts every repeat slider's ball on the
    wrong side.
    """
    kinds = {
        diffcalc.ObjectKind.Circle: "circle",
        diffcalc.ObjectKind.Slider: "slider",
        diffcalc.ObjectKind.Spinner: "spinner",
    }
    _, spans = encode_paths(beatmap)
    payload: list[dict[str, Any]] = []
    for obj, span in zip(beatmap.objects, spans, strict=True):
        entry: dict[str, Any] = {
            "t": obj.time,
            "end": obj.end_time,
            "x": round(obj.x, 2),
            "y": round(obj.y, 2),
            "kind": kinds[obj.kind],
            "combo": obj.new_combo,
        }
        if obj.kind is diffcalc.ObjectKind.Slider:
            entry["p"] = list(span)
            entry["slides"] = obj.slides
        payload.append(entry)
    return payload


@dataclass(frozen=True, slots=True)
class ReplayPayload:
    """Everything a renderer needs for one replay, ready to serialise."""

    header: dict[str, Any]
    frames: bytes

    paths: bytes = b""
    """Slider bodies, interleaved `float32` x, y. Indexed by each slider's `p`."""

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
        paths, _ = encode_paths(beatmap)
        header = {
            "schema_version": SCHEMA_VERSION,
            "replay": replay_name,
            "rate": rate,
            "sample_bytes": SAMPLE_BYTES,
            "sample_count": len(simulation.frames.frames),
            "path_point_bytes": PATH_POINT_BYTES,
            "path_points": len(paths) // PATH_POINT_BYTES,
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
        return cls(
            header=header,
            frames=encode_frames(simulation.frames.frames),
            paths=paths,
        )
