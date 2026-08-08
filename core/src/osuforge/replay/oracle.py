"""Comparing our judgements against a second, independent implementation.

The header screen in :mod:`osuforge.replay.validate` can only compare totals,
and it establishes that a replay was matched to the right beatmap and that
presses were not grossly misaligned. It cannot establish that *this* press
judged *that* object, which is what a hit error means.

Only a second implementation can. circleguard judges the same replays object by
object, from the same public documentation and with no code in common with ours,
so where the two agree both are probably right and where they differ one of them
has a bug worth finding.

# circleguard is not ground truth

It is another simulator with the same problem we have. Run against a real
replay, its counts differed from that replay's own header as well —
573/61/23/10 against 571/66/25/7. Treating it as the answer would just be
believing someone else's guess instead of our own.

What it is good for is **disagreement**. Two implementations failing the same
way is unlikely unless the documentation is what is wrong; two implementations
agreeing on 99% of objects makes a systematic pairing error in either of them
hard to sustain.

# Circles are what is gated on

Agreement is reported for every object and gated on circles only, for the same
reason the header screen holds circles to a stricter standard than sliders: a
circle's judgement follows from a discrete press on a recorded frame, and a
slider's depends on tick collection that a 60 Hz recording cannot resolve. Both
implementations are guessing about sliders, so their agreement there measures
how similar the guesses are rather than whether either is right.

Offset analysis uses circles. Circles are what has to be correct.

# How it runs

circleguard is AGPL-3.0. It is installed into an isolated virtualenv under
`tools/oracle-venv/`, driven by subprocess, and never imported here — this
module contains no reference to it beyond the path to a script. Nothing built
from this repository links it. See `docs/licensing.md`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import osu_forge_diffcalc as diffcalc

from osuforge.replay.simulate import Grade, Simulation

__all__ = [
    "ORACLE_PYTHON_ENV",
    "Disagreement",
    "OracleComparison",
    "OracleUnavailableError",
    "compare",
    "run_oracle",
]

ORACLE_PYTHON_ENV = "OSU_FORGE_ORACLE_PYTHON"
"""Environment variable naming the interpreter with circleguard installed."""


def _repo_root() -> Path:
    """Where `tools/` lives, for a checkout.

    Derived from this file rather than from the working directory, so that
    running the tests from `core/` finds the same script as running them from
    the repository root. For an installed package the path will not exist, and
    the oracle is correctly reported as unavailable — it is a development tool
    and is not shipped.
    """
    return Path(__file__).resolve().parents[4]


_DEFAULT_ORACLE_PYTHON = _repo_root() / "tools" / "oracle-venv" / "Scripts" / "python.exe"
_JUDGE_SCRIPT = _repo_root() / "tools" / "oracle" / "judge.py"


class OracleUnavailableError(RuntimeError):
    """The oracle could not be run.

    Raised rather than returning an empty comparison, because "the oracle found
    no disagreements" and "the oracle did not run" must never be confused: the
    second is a blocker and the first is a pass.
    """


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One object the two implementations graded differently."""

    replay: str
    object_time: int
    kind: str
    ours: Grade | None
    theirs: int | None
    """`None` on either side means that implementation did not judge the object
    at all — circleguard skips spinners, so this is expected for those."""


@dataclass(frozen=True, slots=True)
class OracleComparison:
    """How our judgements compare with circleguard's, object by object."""

    oracle_version: str
    compared: int
    agreed: int
    circles_compared: int
    circles_agreed: int
    unjudged_by_oracle: int
    """Objects we judged that circleguard did not — spinners, mostly."""

    failures: dict[str, str]
    """Replays circleguard could not judge, by path, with its reason."""

    disagreements: list[Disagreement]

    @property
    def agreement(self) -> float:
        return self.agreed / self.compared if self.compared else 0.0

    @property
    def circle_agreement(self) -> float:
        """The figure that gates a recommendation."""
        return self.circles_agreed / self.circles_compared if self.circles_compared else 0.0

    def summary(self) -> str:
        return (
            f"circleguard {self.oracle_version}: "
            f"{self.circle_agreement:.2%} agreement on {self.circles_compared} circles, "
            f"{self.agreement:.2%} on all {self.compared} objects compared "
            f"({self.unjudged_by_oracle} not judged by the oracle, "
            f"{len(self.failures)} replay(s) it could not read)"
        )


def _oracle_python() -> Path:
    configured = os.environ.get(ORACLE_PYTHON_ENV)
    path = Path(configured) if configured else _DEFAULT_ORACLE_PYTHON
    if not path.exists():
        raise OracleUnavailableError(
            f"no interpreter at {path}. Create the isolated environment with\n"
            f"    py -3.14 -m venv tools/oracle-venv\n"
            f"    tools/oracle-venv/Scripts/pip install circleguard\n"
            f"or point {ORACLE_PYTHON_ENV} at one that has circleguard installed. "
            "It is deliberately not a dependency of this package: circleguard is "
            "AGPL-3.0 and is only ever run as a separate process."
        )
    return path


def run_oracle(jobs: list[tuple[Path, Path]], *, timeout: float = 900.0) -> dict[str, Any]:
    """Judge (replay, beatmap) pairs with circleguard in its own process."""
    if not jobs:
        raise OracleUnavailableError("no replay/beatmap pairs to judge")
    # Resolve the interpreter first: a missing environment is the ordinary case
    # and the one with an actionable message, whereas a missing script means the
    # checkout is broken.
    interpreter = _oracle_python()
    script = _JUDGE_SCRIPT
    if not script.exists():
        raise OracleUnavailableError(f"{script} is missing")

    request = json.dumps(
        {"jobs": [{"replay": str(replay), "beatmap": str(beatmap)} for replay, beatmap in jobs]}
    )
    try:
        completed = subprocess.run(
            [str(interpreter), str(script)],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OracleUnavailableError(f"the oracle did not finish within {timeout}s") from exc

    if completed.returncode != 0:
        raise OracleUnavailableError(
            f"the oracle exited {completed.returncode}:\n{completed.stderr.strip()[-2000:]}"
        )
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        # A traceback or a warning on stdout would land here. Reporting the tail
        # of stderr is what makes that diagnosable rather than mystifying.
        raise OracleUnavailableError(
            f"the oracle produced unreadable output: {exc}\n{completed.stderr.strip()[-2000:]}"
        ) from exc
    return parsed


def compare(
    simulations: dict[str, Simulation],
    oracle_output: dict[str, Any],
    *,
    max_disagreements: int = 200,
) -> OracleComparison:
    """Compare our judgements against the oracle's, object by object.

    `simulations` is keyed by replay path, matching the paths given to
    :func:`run_oracle`.
    """
    compared = agreed = circles_compared = circles_agreed = unjudged = 0
    failures: dict[str, str] = {}
    disagreements: list[Disagreement] = []

    for result in oracle_output.get("results", []):
        replay = result["replay"]
        if "error" in result:
            failures[replay] = result["error"]
            continue
        simulation = simulations.get(replay)
        if simulation is None:
            failures[replay] = "no simulation was supplied for this replay"
            continue

        # Objects are matched on their time, not their position in the list:
        # circleguard skips spinners, so the two lists are different lengths.
        theirs = {entry["object_time"]: entry for entry in result["objects"]}
        for hit in simulation.hits:
            entry = theirs.pop(hit.time, None)
            if entry is None:
                unjudged += 1
                continue

            compared += 1
            is_circle = hit.kind is diffcalc.ObjectKind.Circle
            if is_circle:
                circles_compared += 1
            if int(hit.grade) == entry["grade"]:
                agreed += 1
                if is_circle:
                    circles_agreed += 1
            elif len(disagreements) < max_disagreements:
                disagreements.append(
                    Disagreement(
                        replay=replay,
                        object_time=hit.time,
                        kind=str(hit.kind).rsplit(".", maxsplit=1)[-1],
                        ours=hit.grade,
                        theirs=entry["grade"],
                    )
                )

        for leftover in theirs.values():
            # Judged by the oracle and not by us. Counted as a disagreement
            # rather than ignored: an object we never produced is a worse error
            # than one we graded differently.
            compared += 1
            if len(disagreements) < max_disagreements:
                disagreements.append(
                    Disagreement(
                        replay=replay,
                        object_time=leftover["object_time"],
                        kind=leftover["kind"],
                        ours=None,
                        theirs=leftover["grade"],
                    )
                )

    return OracleComparison(
        oracle_version=str(oracle_output.get("version", "unknown")),
        compared=compared,
        agreed=agreed,
        circles_compared=circles_compared,
        circles_agreed=circles_agreed,
        unjudged_by_oracle=unjudged,
        failures=failures,
        disagreements=disagreements,
    )


if __name__ == "__main__":  # pragma: no cover - a convenience entry point
    sys.stderr.write("run this through osuforge.replay.oracle.compare, not directly\n")
    raise SystemExit(2)
