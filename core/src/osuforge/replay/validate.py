"""Checking a simulation against the only ground truth available.

Every `.osr` header records the judgement counts the game produced. A simulator
replaying the same inputs against the same beatmap should reproduce them, and
where it cannot, that has to be established rather than assumed.

This exists in the same commit as :mod:`osuforge.replay.simulate` on purpose. A
simulator merged without its check produces confident, plausible, wrong numbers,
and by the time anyone doubts them there is a layer of analysis built on top.

# What the header can and cannot settle

The design this started from required the miss count to match exactly and every
grade to land within a couple of objects. Measured against a real corpus of 57
replays, that is not achievable — and the reason is a property of the format,
not a defect in the simulation:

- **A stable replay records at about 60 Hz.** Measured over 712,186 frames: a
  16 ms median interval, 18 ms at the 90th percentile. The game judges a slider
  tick on its own update, which is faster, so some ticks are decided from a
  cursor position the replay never recorded. The recording gap containing a
  slider part has a median of 10 ms and reaches 17 ms at the 90th percentile.
- The consequence is measurable. Sliders scoring 300 reproduce to 0.42% across
  15,359 of them, but the split of a *dropped* slider between 100, 50 and miss
  does not, and the per-replay residual runs in **both directions** — up to
  about 13% of a map's slider count either way. Scatter, not bias.
- Two candidate explanations were tested against the corpus and rejected.
  Tracking is not sticky: ending a slider at the first part missed produces 293
  too many misses where the independent model produces 88 too few. And a slider
  is not graded on more than half its parts; requiring that moves over a
  thousand objects from 100 to 50.

So the header settles some things exactly and others not at all:

| Reproducible from a replay          | Not reproducible                    |
|-------------------------------------|-------------------------------------|
| the object count                     | which grade a dropped slider took   |
| a circle's judgement — a press is a  | whether a slider collected any part |
| discrete event on a recorded frame,  | at all, when its head was missed    |
| and that frame is the game's own     |                                     |
| view of the cursor                   |                                     |

# What this gate is therefore for

It is a **screen against gross misalignment**, not a certification of per-object
accuracy. It catches a replay paired with the wrong beatmap, and presses paired
with the wrong objects — both of which produce discrepancies far larger than the
sampling noise. When the position check was missing from the simulator, it
reported 1 miss against the game's 37; that is the size of failure this catches.

The two things it holds exactly are the two that are reproducible:

1. **The object count must match.** Anything else is the wrong beatmap or the
   wrong parse of it, and no comparison after that means anything.
2. **Simulated circle misses may not exceed the game's total misses.** A circle
   miss is reproducible, and the game's total includes sliders and spinners as
   well, so exceeding it is impossible unless presses went to other objects.
   This held on all 57 local replays, including — importantly — while the
   simulator was still badly wrong about sliders.

Grades are compared against an allowance proportional to the map's slider count,
because that is where the irreproducible part lives. The allowance is set from
the spread observed on this corpus rather than derived from first principles,
which is worth saying plainly: it makes this a screen, not a proof.

# The real check, and what it gates

Per-object correctness for circles — which is what offset analysis rests on —
needs an independent oracle that judges the same replays object by object. That
is the differential comparison against circleguard, run in an isolated
environment. Until it has run and agreed, :attr:`CorpusHealth.may_recommend` is
false no matter how well the header screen goes, and it says so.

"I cannot tell you" beats a confident wrong answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from osuforge.replay.model import Judgements
from osuforge.replay.simulate import Grade, Simulation

__all__ = [
    "MINIMUM_ORACLE_AGREEMENT",
    "MINIMUM_USABLE_FRACTION",
    "Agreement",
    "CorpusHealth",
    "Validation",
    "corpus_health",
    "validate",
]

MINIMUM_USABLE_FRACTION = 0.80
"""Below this share of replays passing the screen, the corpus is not usable.

Excluding bad replays one at a time is only safe while most of them are good.
Surviving a filter is not the same as being correct, and the survivors are
exactly the replays whose patterns the simulator handles — a bias in the
direction of whatever it does well.
"""

MINIMUM_ORACLE_AGREEMENT = 0.99
"""Per-object agreement the differential oracle must reach."""

_SLIDER_ALLOWANCE = 0.15
"""Grade discrepancy tolerated, as a share of the map's slider count.

Set from the spread measured on the local corpus, where the worst replay came to
13.6% and the residual ran in both directions. It is not derived from a model of
the sampling, and it is loose enough that it certifies nothing about individual
objects — see the module docstring.
"""

_MINIMUM_ALLOWANCE = 2
"""Floor for maps with few or no sliders, so the allowance is never zero."""


class Agreement(StrEnum):
    EXACT = "exact"
    CLOSE = "close"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class Validation:
    """How one simulation compared against its replay's header."""

    agreement: Agreement
    reason: str
    """Plain-language account of the comparison, including for `EXACT`.

    Always populated, so a report can show why a replay was kept as readily as
    why one was dropped.
    """

    simulated: Judgements
    reported: Judgements
    allowance: int
    """The grade discrepancy this replay's slider count bought it."""

    @property
    def usable(self) -> bool:
        return self.agreement is not Agreement.MISMATCH

    @property
    def differences(self) -> dict[str, int]:
        """Simulated minus reported, per grade. Empty when they agree."""
        return _differences(self.simulated, self.reported)


def _differences(simulated: Judgements, reported: Judgements) -> dict[str, int]:
    deltas = {
        "300": simulated.count_300 - reported.count_300,
        "100": simulated.count_100 - reported.count_100,
        "50": simulated.count_50 - reported.count_50,
        "miss": simulated.count_miss - reported.count_miss,
    }
    return {grade: delta for grade, delta in deltas.items() if delta != 0}


def _as_judgements(simulation: Simulation) -> Judgements:
    counts = simulation.counts()
    # Geki and katu are combo-ending 300s and 100s, already counted in those.
    # They are not simulated and are reported as zero rather than guessed at;
    # nothing compares them, because `Judgements.total` excludes them too.
    return Judgements(
        count_300=counts[Grade.THREE_HUNDRED],
        count_100=counts[Grade.HUNDRED],
        count_50=counts[Grade.FIFTY],
        count_geki=0,
        count_katu=0,
        count_miss=counts[Grade.MISS],
    )


def validate(simulation: Simulation, reported: Judgements) -> Validation:
    """Screen a simulation against the judgement counts in a replay header."""
    simulated = _as_judgements(simulation)
    allowance = max(_MINIMUM_ALLOWANCE, math.ceil(_SLIDER_ALLOWANCE * simulation.sliders))

    def result(agreement: Agreement, reason: str) -> Validation:
        return Validation(
            agreement=agreement,
            reason=reason,
            simulated=simulated,
            reported=reported,
            allowance=allowance,
        )

    if simulated.total != reported.total:
        return result(
            Agreement.MISMATCH,
            f"judged {simulated.total} objects, the game judged {reported.total}. "
            "The object lists differ, so this is the wrong beatmap or the wrong "
            "parse of it, and no comparison after that means anything.",
        )

    if simulation.circle_misses > reported.count_miss:
        return result(
            Agreement.MISMATCH,
            f"{simulation.circle_misses} circles were missed, more than the "
            f"{reported.count_miss} misses the game recorded across every object. "
            "A circle's judgement is reproducible, so this is impossible unless "
            "presses were paired with objects the game paired differently — and "
            "then every error in this replay is measured against the wrong object.",
        )

    differences = _differences(simulated, reported)
    if not differences:
        return result(Agreement.EXACT, f"all {reported.total} judgements reproduce")

    worst = max(abs(delta) for delta in differences.values())
    if worst <= allowance:
        return result(
            Agreement.CLOSE,
            f"object count and the circle-miss bound both hold; grades differ by at "
            f"most {worst}, within the {allowance} this map's {simulation.sliders} "
            "sliders allow. The split of a dropped slider between 100, 50 and miss "
            "is not reproducible from a 60 Hz recording.",
        )

    return result(
        Agreement.MISMATCH,
        f"grades differ by {worst}, past the {allowance} allowed by this map's "
        f"{simulation.sliders} sliders. Too large for the part of slider scoring "
        "that a replay cannot resolve.",
    )


@dataclass(frozen=True, slots=True)
class CorpusHealth:
    """Whether a set of replays is fit to draw a conclusion from."""

    total: int
    exact: int
    close: int
    mismatched: int

    fractional_window_replays: int
    """Replays whose beatmap has a non-integer judgement window.

    stable compares against an integer and how it rounds is unverified. When this
    is zero the question cannot have affected any of these numbers, which is a
    stronger statement than assuming it did not.
    """

    oracle_agreement: float | None = None
    """Per-object agreement with the differential oracle, if it has been run.

    ``None`` means it has not. That is not the same as passing, and
    :attr:`may_recommend` treats it as a blocker: the header screen cannot
    establish per-object correctness, so nothing may be recommended on its
    strength alone.
    """

    @property
    def usable(self) -> int:
        return self.exact + self.close

    @property
    def usable_fraction(self) -> float:
        return self.usable / self.total if self.total else 0.0

    @property
    def screen_passed(self) -> bool:
        return self.total > 0 and self.usable_fraction >= MINIMUM_USABLE_FRACTION

    @property
    def oracle_passed(self) -> bool:
        return (
            self.oracle_agreement is not None and self.oracle_agreement >= MINIMUM_ORACLE_AGREEMENT
        )

    @property
    def may_recommend(self) -> bool:
        """Whether anything may be recommended from these replays.

        This gates the recommendation, not the display. Numbers from the usable
        replays can still be shown, clearly labelled — what is withheld is the
        step from a measurement to "change your offset to X".
        """
        return self.screen_passed and self.oracle_passed

    def blockers(self) -> list[str]:
        """Why no recommendation may be issued, or empty if one may."""
        reasons = []
        if self.total == 0:
            reasons.append("no replays were simulated")
        elif not self.screen_passed:
            reasons.append(
                f"only {self.usable_fraction:.0%} of replays passed the header screen, "
                f"below the {MINIMUM_USABLE_FRACTION:.0%} floor. The simulator does not "
                "reproduce this data well enough for the replays that did pass to be "
                "a fair sample of it"
            )
        if self.oracle_agreement is None:
            reasons.append(
                "the differential oracle has not been run. The header screen cannot "
                "establish that presses were paired with the right objects one by one, "
                "and that is what an offset measurement rests on"
            )
        elif not self.oracle_passed:
            reasons.append(
                f"the differential oracle agreed on {self.oracle_agreement:.1%} of "
                f"objects, below the {MINIMUM_ORACLE_AGREEMENT:.0%} required"
            )
        return reasons

    def summary(self) -> str:
        if self.total == 0:
            return "no replays simulated"
        parts = [
            f"{self.usable}/{self.total} replays passed the screen "
            f"({self.usable_fraction:.0%}): {self.exact} exact, {self.close} close, "
            f"{self.mismatched} excluded"
        ]
        if self.fractional_window_replays:
            parts.append(
                f"{self.fractional_window_replays} replay(s) on beatmaps with a "
                "fractional hit window, where the unverified rounding rule could matter"
            )
        parts.extend(f"no recommendation: {reason}" for reason in self.blockers())
        return "; ".join(parts)


def corpus_health(
    results: list[tuple[Simulation, Validation]],
    *,
    oracle_agreement: float | None = None,
) -> CorpusHealth:
    """Aggregate per-replay validations into a decision about the whole corpus."""
    exact = sum(1 for _, v in results if v.agreement is Agreement.EXACT)
    close = sum(1 for _, v in results if v.agreement is Agreement.CLOSE)
    mismatched = sum(1 for _, v in results if v.agreement is Agreement.MISMATCH)
    fractional = sum(1 for s, _ in results if s.fractional_windows)
    return CorpusHealth(
        total=len(results),
        exact=exact,
        close=close,
        mismatched=mismatched,
        fractional_window_replays=fractional,
        oracle_agreement=oracle_agreement,
    )
