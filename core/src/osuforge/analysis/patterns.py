"""Where the accuracy went.

Not "you are bad at streams". This answers a narrower question that can actually
be answered from a replay: of everything you did not score, which kinds of
object accounted for it.

# It is arithmetic, not inference

Accuracy loss is exact. Every object scored 300, 100, 50 or nothing, so the
shortfall against a perfect play is a sum, and attributing that sum to groups of
objects is a partition. The parts add to the whole by construction. Nothing here
fits a model, tests a hypothesis or claims a cause — a group can be reported as
having cost eight percent of the play without anyone having to believe a theory
about why.

That is a deliberately low ceiling. It also makes the output hard to argue with,
which is worth more here than a stronger claim would be.

# Groups are cut within one map at a time

Every feature used to group objects — how fast the cursor has to move, how sharp
the turn is, whether the rhythm just changed — is computed from the beatmap and
compared against the rest of *that* beatmap. A player's approach rate, circle
size, star rating, session and settings are all constant inside a map, so a
within-map contrast controls for every one of them without having to model any.

Comparing across maps would confound all of it at once. In a corpus of 45 maps
those factors are collinear enough that no amount of care separates them.

# What this cannot say

**That a pattern is one you "cannot do".** Outcomes are visible; capability is
not. A pattern you meet twice and miss twice is not evidence of anything, which
is why exposure is reported beside every share and why nothing is said about a
group that is too small.

**That you are worse at something than other players are.** Hard patterns are
hard for everyone, and there is no per-object data for anyone else to compare
against. Everything here is you against the rest of your own play.

**That a repeated mistake is repeated.** That needs the same map played
repeatedly, and it needs the attempts that were quick-retried away — which leave
no replay at all, and are exactly the attempts where the mistake happened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import osu_forge_diffcalc as diffcalc

from osuforge.replay.simulate import Grade, Simulation

__all__ = [
    "CONCENTRATION",
    "MAJORITY",
    "MINIMUM_GROUP",
    "Attribution",
    "ObjectContext",
    "Share",
    "attribute",
    "contexts_for",
]

CONCENTRATION = 1.25
"""How much more than its share of the loss a group has to carry to be named.

A group holding 90% of the objects and 95% of the loss is at 1.06 and is not a
finding — it is the play. The threshold is a judgement about what is worth a
player's attention rather than a significance test, and it is stated here
instead of being buried in a sort.
"""

MAJORITY = 0.5
"""A group larger than this is not an explanation, whatever its numbers say.

"Most of the map is where you lost accuracy" is true of every play.
"""

MINIMUM_GROUP = 30
"""Objects a group needs before its share is reported.

Below this the share is still counted into the total — dropping it would break
the partition — but it is not named, because a group of six objects that cost
four percent of a play is a coincidence with a label on it.
"""


@dataclass(frozen=True, slots=True)
class ObjectContext:
    """What the map asks for at one object, relative to the rest of the map."""

    index: int
    velocity: float
    """Osu! pixels per millisecond the cursor has to average to arrive in time.

    The single most informative scalar for aim demand: it folds distance and
    time together, and a jump map and a stream map differ in it in the direction
    you would expect.
    """

    spacing: float
    """Distance from the previous object, in radii. Circle size cancels."""

    angle: float | None
    """The angle at the *previous* object, in radians, formed by the three
    positions.

    osu!'s own convention, which is worth matching rather than inventing a
    second one: the angle is measured between the two vectors leaving the middle
    object, so a straight line through it is pi and doubling back on yourself is
    zero. **Small is a sharp reversal.**

    A first version measured the angle between the two movement vectors instead,
    which is the same quantity subtracted from pi — and then labelled small
    values "sharp reversals", which they were not. Every reversal in the data
    came out at 160 to 180 degrees and was filed under "everything else".

    `None` for the first two objects of a map and across any gap long enough
    that the previous object is not part of the same motion.
    """

    rhythm_ratio: float | None
    """This gap divided by the previous one. Far from 1 is a rhythm change, which
    is where a tech map does its damage."""

    visible: int
    """How many objects are on screen when this one is due, including it.

    Reading load, and it varies *within* a map — unlike the approach rate, which
    does not vary at all and is therefore inseparable from the map itself.
    """


def contexts_for(beatmap: diffcalc.Beatmap) -> list[ObjectContext]:
    """Compute the local context of every object on a beatmap."""
    objects = beatmap.objects
    preempt = beatmap.preempt
    radius = beatmap.radius
    contexts: list[ObjectContext] = []

    # Objects are in time order, so the window of visible ones only ever moves
    # forward. Counting them with a sliding index keeps this linear instead of
    # quadratic, which matters on a map with four thousand objects.
    first_visible = 0
    for index, obj in enumerate(objects):
        while objects[first_visible].end_time < obj.time - preempt:
            first_visible += 1
        visible = index - first_visible + 1

        if index == 0:
            contexts.append(ObjectContext(index, 0.0, 0.0, None, None, visible))
            continue

        previous = objects[index - 1]
        gap = max(1.0, obj.time - previous.end_time)
        distance = math.dist((obj.x, obj.y), (previous.x, previous.y))
        velocity = distance / gap
        spacing = distance / radius if radius > 0 else 0.0

        angle: float | None = None
        rhythm: float | None = None
        if index >= 2:
            before = objects[index - 2]
            previous_gap = max(1.0, previous.time - before.end_time)
            rhythm = gap / previous_gap
            # A turn only means something when the two motions belong together.
            # Across a break, the "angle" is between a movement and a rest.
            if gap < 2000.0 and previous_gap < 2000.0:
                # Both vectors leave the middle object, so the result is the
                # angle you would measure with a protractor at it: pi for a
                # straight line, zero for doubling back.
                incoming = (before.x - previous.x, before.y - previous.y)
                outgoing = (obj.x - previous.x, obj.y - previous.y)
                if any(incoming) and any(outgoing):
                    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
                    norm = math.hypot(*incoming) * math.hypot(*outgoing)
                    angle = math.acos(max(-1.0, min(1.0, dot / norm)))

        contexts.append(ObjectContext(index, velocity, spacing, angle, rhythm, visible))

    return contexts


@dataclass(frozen=True, slots=True)
class Share:
    """One group of objects and what it cost."""

    feature: str
    label: str
    objects: int
    share_of_objects: float
    share_of_loss: float
    """Fraction of the play's total accuracy shortfall that fell here.

    Compare against `share_of_objects`. A group holding 8% of the objects and
    42% of the loss is doing five times its share of the damage; one holding 8%
    of both is doing exactly its share and says nothing.
    """

    accuracy: float
    """Accuracy within the group, for comparison against the play's own."""

    reportable: bool
    """False for a group too small to name. Its loss still counts toward the
    total, because dropping it would stop the parts adding to the whole."""

    @property
    def concentration(self) -> float:
        """Loss share divided by object share. 1.0 is unremarkable."""
        if self.share_of_objects <= 0.0:
            return math.nan
        return self.share_of_loss / self.share_of_objects


@dataclass(frozen=True, slots=True)
class Attribution:
    """Where a play's accuracy went, partitioned several ways."""

    accuracy: float
    total_loss: float
    """Accuracy points lost, where one missed object is 1.0 and a 100 is 2/3."""

    judged: int
    shares: list[Share]

    def worst(self, limit: int = 3) -> list[Share]:
        """Groups doing enough more damage than their size to be worth naming.

        Two filters, and both were added after a first version reported "the
        other 90% of the map" as a finding. A group has to be a minority of the
        play — a group that is most of the map explains nothing, whatever its
        numbers — and it has to be concentrated by a margin rather than by a
        rounding error.
        """
        candidates = [
            share
            for share in self.shares
            if share.reportable
            and share.share_of_objects <= MAJORITY
            and share.concentration >= CONCENTRATION
        ]
        return sorted(candidates, key=lambda share: -share.share_of_loss)[:limit]


_LOSS = {Grade.THREE_HUNDRED: 0.0, Grade.HUNDRED: 2 / 3, Grade.FIFTY: 5 / 6, Grade.MISS: 1.0}


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[position]


def attribute(simulation: Simulation, beatmap: diffcalc.Beatmap) -> Attribution:
    """Partition a play's accuracy loss across groups of objects."""
    contexts = contexts_for(beatmap)
    hits = [hit for hit in simulation.hits if hit.index < len(contexts)]
    if not hits:
        return Attribution(accuracy=0.0, total_loss=0.0, judged=0, shares=[])

    losses = [_LOSS[hit.grade] for hit in hits]
    total_loss = sum(losses)
    judged = len(hits)
    accuracy = 1.0 - total_loss / judged if judged else 0.0

    shares: list[Share] = []

    def add(feature: str, buckets: dict[str, list[int]]) -> None:
        for label, indices in buckets.items():
            if not indices:
                continue
            group_loss = sum(losses[i] for i in indices)
            shares.append(
                Share(
                    feature=feature,
                    label=label,
                    objects=len(indices),
                    share_of_objects=len(indices) / judged,
                    share_of_loss=group_loss / total_loss if total_loss > 0 else 0.0,
                    accuracy=1.0 - group_loss / len(indices),
                    reportable=len(indices) >= MINIMUM_GROUP,
                )
            )

    # Cut points come from this map's own distribution, so "fast" means fast for
    # this map. A fixed threshold would put every object on an easy map in one
    # bucket and say nothing.
    velocities = [contexts[hit.index].velocity for hit in hits]
    fast = _quantile(velocities, 0.90)
    add(
        "cursor velocity",
        {
            f"fastest 10% of this map (over {fast:.2f} px/ms)": [
                position for position, hit in enumerate(hits) if contexts[hit.index].velocity > fast
            ],
            f"the slower 90% (under {fast:.2f} px/ms)": [
                position
                for position, hit in enumerate(hits)
                if contexts[hit.index].velocity <= fast
            ],
        },
    )

    # Every label has to make sense on its own, including the complement. An
    # earlier version called this one "everything else", which told a reader
    # nothing at the moment they most needed telling.
    add(
        "turn",
        {
            "sharp reversals (turning back under 60 degrees)": [
                position
                for position, hit in enumerate(hits)
                if (angle := contexts[hit.index].angle) is not None and angle < math.pi / 3
            ],
            "gentle turns and straight runs": [
                position
                for position, hit in enumerate(hits)
                if (angle := contexts[hit.index].angle) is None or angle >= math.pi / 3
            ],
        },
    )

    add(
        "rhythm",
        {
            "first object after a rhythm change": [
                position
                for position, hit in enumerate(hits)
                if (ratio := contexts[hit.index].rhythm_ratio) is not None
                and (ratio > 1.4 or ratio < 0.7)
            ],
            "steady rhythm": [
                position
                for position, hit in enumerate(hits)
                if (ratio := contexts[hit.index].rhythm_ratio) is None or 0.7 <= ratio <= 1.4
            ],
        },
    )

    crowded = _quantile([float(contexts[hit.index].visible) for hit in hits], 0.90)
    add(
        "reading load",
        {
            f"most crowded 10% (over {crowded:.0f} objects on screen)": [
                position
                for position, hit in enumerate(hits)
                if contexts[hit.index].visible > crowded
            ],
            f"the less crowded 90% ({crowded:.0f} or fewer on screen)": [
                position
                for position, hit in enumerate(hits)
                if contexts[hit.index].visible <= crowded
            ],
        },
    )

    return Attribution(accuracy=accuracy, total_loss=total_loss, judged=judged, shares=shares)
