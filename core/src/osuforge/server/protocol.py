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

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import osu_forge_diffcalc as diffcalc

from osuforge.analysis.clustering import ClusteredMean
from osuforge.analysis.corpus import Diagnosis
from osuforge.analysis.progress import Progress
from osuforge.replay.model import ReplayFrame
from osuforge.replay.simulate import Grade, Simulation
from osuforge.replay.validate import CorpusHealth

__all__ = [
    "FRAME_STRUCT",
    "PATH_POINT_BYTES",
    "SAMPLE_BYTES",
    "SCHEMA_VERSION",
    "ReplayPayload",
    "corpus_payload",
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
            # The scoring parts, positioned: ticks, repeats and the tail, in
            # time order — the same order the simulator judges them, so the
            # per-part outcomes in the judgement align by index. Without these
            # a renderer can draw the body but not the reasons it breaks.
            entry["parts"] = [
                [round(part.time, 1), round(part.x, 2), round(part.y, 2), _PART_KINDS[part.kind]]
                for part in obj.parts
            ]
        payload.append(entry)
    return payload


_PART_KINDS = {
    diffcalc.PartKind.Tick: 0,
    diffcalc.PartKind.Repeat: 1,
    diffcalc.PartKind.Tail: 2,
}


_KIND_NAMES = {
    diffcalc.ObjectKind.Circle: "circle",
    diffcalc.ObjectKind.Slider: "slider",
    diffcalc.ObjectKind.Spinner: "spinner",
}


def _finite(value: float) -> float | None:
    """`None` for NaN and infinity, which have no JSON spelling.

    The middleware serialises with `allow_nan` off, so an unguarded NaN is a
    500 on the whole response rather than a hole in one field.
    """
    return value if math.isfinite(value) else None


def analysis_payload(play: Any) -> dict[str, Any]:
    """One play's analysis, as the page needs it.

    Everything here is already computed — this is a shape, not a calculation.
    It travels in the header rather than behind its own endpoint because it is
    a couple of kilobytes and because a list that shows the unstable rate would
    otherwise need a request per row.

    Two things are carried that a naive summary would drop, and both are the
    difference between a number and an honest one:

    `agreement` says whether the simulation reproduced the game's own
    judgement counts. A play it did not reproduce still has an unstable rate,
    and that rate describes something other than what happened.

    `combo_caveat` is why the slider-break count is being withheld. The
    detection is known wrong against the header's max combo, so the count is
    hidden with its reason rather than shown as though it were sound.
    """
    return {
        "accuracy": play.accuracy,
        "mean_error": None if math.isnan(play.mean_error) else play.mean_error,
        "unstable_rate": None if math.isnan(play.unstable_rate) else play.unstable_rate,
        "hits": len(play.errors),
        "errors": [round(error, 2) for error in play.errors],
        "aim_errors": [round(value, 3) for value in play.aim_errors],
        "key_balance": None if math.isnan(play.key_balance) else play.key_balance,
        "presses": {str(button): count for button, count in play.presses.items()},
        "agreement": str(play.agreement),
        "agreement_reason": play.agreement_reason,
        "usable": play.usable,
        "max_combo": play.max_combo,
        "reported_max_combo": play.reported_max_combo,
        "sliderbreaks": play.sliderbreaks,
        "combo_caveat": play.combo_caveat,
        "bpm": play.bpm,
        "length_ms": play.length_ms,
        "object_count": play.object_count,
        "played_at": play.played_at.isoformat(),
        "findings": [
            {
                "feature": share.feature,
                "label": share.label,
                "objects": share.objects,
                "share_of_objects": share.share_of_objects,
                "share_of_loss": share.share_of_loss,
                "accuracy": share.accuracy,
            }
            for share in play.findings()
        ],
        "breaks": [
            {
                "time": failure.time,
                "kind": _KIND_NAMES.get(failure.kind, "circle"),
                "grade": int(failure.grade),
                "combo_lost": failure.combo_lost,
                # How late, and how far off centre. A break with an error of
                # +58 ms and a miss with no press at all are different
                # mistakes, and the difference is the answer to why it broke.
                "error": failure.error,
                "aim_error": (None if failure.aim_error is None else round(failure.aim_error, 3)),
                "parts_collected": failure.parts_collected,
                "parts_total": failure.parts_total,
            }
            for failure in play.breaks
        ],
    }


def _estimate_block(found: ClusteredMean) -> dict[str, Any]:
    return {
        "mean": _finite(found.mean),
        "ci_low": _finite(found.ci_low),
        "ci_high": _finite(found.ci_high),
        "replays": found.n_replays,
        "sessions": found.n_sessions,
        "hits": found.n_hits,
    }


def _progress_block(found: Progress) -> dict[str, Any]:
    """The corpus over time, as the page needs it.

    The per-session points are descriptions and travel without intervals; the
    only interval in this block sits on the difference, because that is the
    only place there are enough sessions to earn one.
    """
    boundary: dict[str, Any] | None = None
    if found.boundary_kind is not None and found.boundary_at is not None:
        boundary = {
            "kind": found.boundary_kind,
            "at": found.boundary_at.isoformat(),
            "label": found.boundary_label,
        }
    shift = found.shift
    return {
        "points": [
            {
                "session": point.session,
                "started_at": point.started_at.isoformat(),
                "replays": point.replays,
                "hits": point.hits,
                "mean_error": _finite(point.mean_error),
                "spread_ms": _finite(point.spread_ms),
            }
            for point in found.points
        ],
        "boundary": boundary,
        "shift": (
            None
            if shift is None
            else {
                "before": _estimate_block(shift.before),
                "after": _estimate_block(shift.after),
                "difference": _finite(shift.difference),
                "ci_low": _finite(shift.ci_low),
                "ci_high": _finite(shift.ci_high),
                "ci_source": shift.ci_source,
                "spread_before": _finite(shift.spread_before),
                "spread_after": _finite(shift.spread_after),
                "moved": shift.moved,
                "toward_zero": shift.toward_zero,
                "verdict": shift.verdict(),
            }
        ),
        "insufficient": found.insufficient,
    }


def corpus_payload(
    diagnosis: Diagnosis,
    *,
    per_beatmap: dict[str, ClusteredMean],
    beatmaps_played: int,
    health: CorpusHealth,
    unreproduced: dict[str, str],
    local_offsets_known: bool,
    reading: str | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """The corpus diagnosis, as the page needs it.

    A shape, not a calculation — everything here was computed by
    :mod:`osuforge.analysis.corpus` and :mod:`osuforge.replay.validate`, and
    this only decides what travels and under which name.

    Two honesty rails are part of the shape rather than optional extras.
    `health` rides on every answer because the axes can be statistically sound
    while the corpus they came from is not — `may_recommend` is what separates
    a measurement from advice, and a page without it would show the first as
    the second. And `insufficient` is a sentence rather than an absent key,
    because "not enough data" and "nothing wrong" are opposite conclusions
    that look identical as an empty panel.

    `local_offsets_known` is a third of the same kind, named as
    `forge diagnose` names it in its JSON. A corpus whose offsets were read and
    a corpus that assumed them zero look identical in every other number here,
    and only one of them has actually excluded the maps the player nudged.
    """
    bias = diagnosis.bias
    timing = diagnosis.timing
    return {
        "replays": diagnosis.replays,
        "sessions": diagnosis.sessions,
        "hits": diagnosis.hits,
        "summary": diagnosis.summary(),
        "insufficient": diagnosis.insufficient,
        "effective_hits": _finite(diagnosis.effective_hits),
        "design_effect": None if bias is None else _finite(bias.design_effect),
        "bias": (
            None
            if bias is None
            else {
                "mean": _finite(bias.mean),
                "ci_low": _finite(bias.ci_low),
                "ci_high": _finite(bias.ci_high),
                "ci_source": bias.ci_source,
            }
        ),
        "unstable_rate": None if timing is None else _finite(timing.unstable_rate),
        "spread_ms": None if timing is None else _finite(timing.spread_ms),
        "axes": [
            {
                "name": axis.name,
                "verdict": axis.verdict,
                "actionable": axis.actionable,
                "detail": axis.detail,
                "evidence": list(axis.evidence),
            }
            for axis in diagnosis.axes
        ],
        # One dictionary, because the page asks one question — "which plays are
        # not in this answer, and why" — and whether the screen or the inclusion
        # policy did the excluding is carried by the reason text itself.
        "excluded": {**unreproduced, **diagnosis.dropped},
        "local_offsets_known": local_offsets_known,
        "health": {
            "total": health.total,
            "usable": health.usable,
            "may_recommend": health.may_recommend,
            "blockers": health.blockers(),
            "summary": health.summary(),
        },
        # Absent rather than empty when the caller did not compute it, so a
        # page can tell "no progress view here" from "nothing has changed".
        "progress": None if progress is None else _progress_block(progress),
        "beatmaps": {
            "played": beatmaps_played,
            "reading": reading,
            # Most-played first, so the map with the most evidence leads. Ties
            # break on the name rather than on dict order, or two runs over the
            # same corpus could disagree about a purely presentational thing.
            "reported": [
                {
                    "name": name,
                    "mean": _finite(found.mean),
                    "ci_low": _finite(found.ci_low),
                    "ci_high": _finite(found.ci_high),
                    "replays": found.n_replays,
                    "excludes_zero": found.excludes_zero,
                }
                for name, found in sorted(
                    per_beatmap.items(), key=lambda item: (-item[1].n_replays, item[0])
                )
            ],
        },
    }


@dataclass(frozen=True, slots=True)
class ReplayPayload:
    """Everything a renderer needs for one replay, ready to serialise."""

    header: dict[str, Any]
    frames: bytes

    paths: bytes = b""
    """Slider bodies, interleaved `float32` x, y. Indexed by each slider's `p`."""

    background: Path | None = None
    """The map's background image on disk, when it has one that exists.

    A path rather than bytes, resolved when the payload was prepared: a few
    megabytes of JPEG per replay held resident would let the songs folder
    decide the server's memory footprint, while a path read on request costs
    one disk read for the one replay being watched. The request only names a
    replay; which file that means was decided here, not by the asker.
    """

    @classmethod
    def build(
        cls,
        *,
        replay_name: str,
        beatmap: diffcalc.Beatmap,
        simulation: Simulation,
        rate: float,
        analysis: dict[str, Any] | None = None,
        background: Path | None = None,
        mods: int = 0,
    ) -> ReplayPayload:
        counts = simulation.counts()
        paths, _ = encode_paths(beatmap)
        header = {
            "schema_version": SCHEMA_VERSION,
            "replay": replay_name,
            "rate": rate,
            # The raw bitmask, so the page can say what the player saw and
            # render it. The beatmap block below already carries the modded
            # values; without this the page could apply them without being
            # able to name them.
            "mods": mods,
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
            # Absent rather than empty when the analysis could not be run, so a
            # page can tell "no findings" from "not analysed".
            "analysis": analysis,
            "objects": objects_payload(beatmap),
            # Judgement per object, aligned with `objects` by position, so the
            # renderer can colour what happened without re-deriving it.
            "judgements": [
                {
                    "grade": int(hit.grade),
                    "error": hit.error,
                    "aim": None if hit.aim_error is None else round(hit.aim_error, 3),
                    # Per-part outcomes for a slider, aligned by index with the
                    # object's `parts`. 0/1 rather than booleans purely for
                    # wire size — a marathon map carries thousands of these.
                    "parts": (
                        None if hit.parts_hit is None else [1 if p else 0 for p in hit.parts_hit]
                    ),
                }
                for hit in simulation.hits
            ],
        }
        return cls(
            header=header,
            frames=encode_frames(simulation.frames.frames),
            paths=paths,
            background=background,
        )
