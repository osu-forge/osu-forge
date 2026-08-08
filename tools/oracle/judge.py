"""Judge replays with circleguard, for differential comparison against ours.

**This file is a development tool and is not part of the distributed package.**
It is the only thing in this repository that touches circleguard, which is
AGPL-3.0. It runs inside an isolated virtualenv under `tools/oracle-venv/`, is
driven by subprocess, is not importable from `osuforge`, is not listed in any
dependency extra, and nothing built from this repository links it. See
`docs/licensing.md`.

Reads a JSON job on stdin::

    {"jobs": [{"replay": "<path.osr>", "beatmap": "<path.osu>"}, ...]}

Writes JSON on stdout: one record per replay, each with a list of per-object
judgements. Errors are values in the output rather than exceptions, so one bad
replay does not cost the whole run.

circleguard is **not ground truth.** It is a second independent implementation,
which is what makes disagreement informative: where two implementations written
from the same public documentation agree, both are probably right, and where
they differ, one of them has a bug worth finding. On the first replay tried, its
counts differed from that replay's own header too — 573/61/23/10 against
571/66/25/7 — so it cannot be treated as the answer.
"""

from __future__ import annotations

import json
import sys
import traceback

import circleguard
import slider

_TYPES = {
    "JudgmentType.Hit300": 300,
    "JudgmentType.Hit100": 100,
    "JudgmentType.Hit50": 50,
    "JudgmentType.Miss": 0,
}


def judge(
    guard: circleguard.KeylessCircleguard, replay_path: str, beatmap_path: str
) -> dict:
    replay = circleguard.ReplayPath(replay_path)
    guard.load(replay)
    beatmap = slider.Beatmap.from_path(beatmap_path)

    def number(value: object, cast: type) -> object:
        # circleguard reads frames into numpy arrays, so these come back as
        # numpy scalars, which json refuses. Casting here rather than reaching
        # for a custom encoder keeps the wire format plain.
        return None if value is None else cast(value)  # type: ignore[call-arg]

    objects = []
    for judgment in guard.judgments(replay, beatmap=beatmap):
        hitobject = judgment.hitobject
        objects.append(
            {
                # The hit object's own time is the only stable key: circleguard
                # skips spinners, so positions in the two lists do not line up.
                "object_time": int(hitobject.time),
                "kind": type(hitobject).__name__,
                "grade": _TYPES[str(judgment.type)],
                # Present only for a hit; a miss has no press to report.
                "press_time": number(getattr(judgment, "t", None), int),
                "x": number(getattr(judgment, "x", None), float),
                "y": number(getattr(judgment, "y", None), float),
            }
        )
    return {
        "replay": replay_path,
        "beatmap": beatmap_path,
        "objects": objects,
    }


def main() -> int:
    request = json.load(sys.stdin)
    guard = circleguard.KeylessCircleguard()
    results = []
    for job in request["jobs"]:
        try:
            results.append(judge(guard, job["replay"], job["beatmap"]))
        except Exception:  # noqa: BLE001
            results.append(
                {
                    "replay": job["replay"],
                    "beatmap": job["beatmap"],
                    "error": traceback.format_exc(limit=3),
                }
            )
    json.dump({"version": circleguard.__version__, "results": results}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
