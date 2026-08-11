"""The verdict as a caller shows it: the boundary to score, and the shape it travels in.

:mod:`osuforge.analysis.verify` decides whether a settings change did what it
predicted. This module is the small amount of work on either side of that:
turning a progress boundary plus the journal's record of what changed into a
:class:`~osuforge.analysis.verify.Boundary`, and turning the comparison back
into the JSON that `forge diagnose --json` and the served corpus panel both
send.

It lives here rather than in either caller because the two must say the same
thing about the same journal. The boundary whose settings were never written
down is the sharp case: an empty diff reads to `compare` as "changed something
other than the offset", which is a claim about a record that does not exist, so
the sentence that replaces it has to be written once rather than twice. Saying
the same thing includes being handed the corpus in the same order, which the
two callers do not do, so the ordering is imposed here rather than trusted.

The journal is read by the caller, not here. `forge diagnose` reads it per run
and `forge serve` reads it once at startup, and a module that took a `Journal`
would make the server re-read the file behind every recompute.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from osuforge.analysis.verify import (
    BOOTSTRAP_RESAMPLES,
    Boundary,
    Comparison,
    Verdict,
    compare,
)
from osuforge.collect.epoch import ConfigEpoch

if TYPE_CHECKING:
    from osuforge.analysis.corpus import Entry
    from osuforge.analysis.progress import Progress

__all__ = ["UNRECORDED_CHANGE", "verification_json", "verify_boundary"]


UNRECORDED_CHANGE = (
    "What this boundary changed was not recorded: the journal predates settings "
    "snapshots, so the two fingerprints differ but the values behind them are gone. "
    "Read the line above as no prediction to check rather than as the offset having "
    "stayed where it was. Boundaries from here on carry their settings with them."
)
"""What a boundary whose settings are missing says instead of a prediction."""


def _finite(value: float | None) -> float | None:
    """`None` for a NaN, so JSON carries "not measured" rather than a token
    only some parsers accept and none of them agree about.

    `None` passes through, because :class:`~osuforge.analysis.verify.Comparison`
    leaves every number NaN when it refuses and its `predicted` is optional
    besides: one guard covers both.
    """
    return value if value is not None and math.isfinite(value) else None


def verify_boundary(
    entries: list[Entry],
    across: Progress,
    settings: dict[str, dict[str, str]],
    *,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[Comparison, str | None] | None:
    """Score a recorded settings change against the prediction it made.

    `settings` maps a fingerprint to the settings behind it, as
    :meth:`osuforge.collect.journal.Journal.epoch_settings` returns it. A
    fingerprint absent from it was recorded before snapshots existed.

    `entries` may arrive in any order. The two callers hand over two different
    ones and the answer must not depend on which.

    Returns the comparison and, when the settings behind either fingerprint
    were never written down, the sentence that says so. `None` when the split
    is not a settings change at all: the middle of the sessions is a
    description of time and predicts nothing about the mean.
    """
    if across.boundary_at is None or across.before_epoch is None or across.after_epoch is None:
        return None

    before_settings = settings.get(across.before_epoch)
    after_settings = settings.get(across.after_epoch)

    changed: dict[str, tuple[str, str]] = {}
    unrecorded: str | None = UNRECORDED_CHANGE
    if before_settings is not None and after_settings is not None:
        unrecorded = None
        # Reconstructed epochs rather than a second diff, so that "a setting
        # appearing for the first time counts as a change" cannot drift
        # between here and the fingerprint it came from.
        changed = ConfigEpoch(digest=across.after_epoch, settings=after_settings).differences(
            ConfigEpoch(digest=across.before_epoch, settings=before_settings)
        )

    # Ordered here rather than taken as given, the way `progress` orders before
    # it splits. `forge diagnose` gathers alphabetically by file name and the
    # served panel by when the replay was played, and the bootstrap route
    # inside `compare` resamples sessions and then replays out of the lists it
    # is handed, drawing from one generator: two orders of one corpus walk that
    # generator differently and arrive at different intervals, far enough apart
    # to return `confirmed` on one road and `unchanged` on the other. The file
    # name breaks ties, because `sorted` is stable and two replays sharing a
    # timestamp would otherwise leave the caller's order deciding again.
    ordered = sorted(entries, key=lambda entry: (entry.played_at, entry.replay))

    comparison = compare(
        Boundary(
            before_epoch=across.before_epoch,
            after_epoch=across.after_epoch,
            before=[entry.sample for entry in ordered if entry.played_at < across.boundary_at],
            after=[entry.sample for entry in ordered if entry.played_at >= across.boundary_at],
            changed=changed,
        ),
        seed=seed,
        resamples=resamples,
    )
    return comparison, unrecorded


def verification_json(
    verification: tuple[Comparison, str | None] | None,
) -> dict[str, Any] | None:
    """One comparison as JSON, or `None` where there was no settings change."""
    if verification is None:
        return None
    comparison, unrecorded = verification

    reason = comparison.reason
    if unrecorded is not None:
        # Replaced rather than appended where the module made no prediction: it
        # reads an empty diff as "changed something other than the offset",
        # which is a claim about settings nobody wrote down. A refusal for
        # thinness is about the replays instead and stands on its own.
        reason = (
            f"{comparison.reason} {unrecorded}"
            if comparison.verdict is Verdict.INSUFFICIENT
            else unrecorded
        )

    return {
        "verdict": str(comparison.verdict),
        "before_mean": _finite(comparison.before_mean),
        "after_mean": _finite(comparison.after_mean),
        "difference": _finite(comparison.difference),
        "ci_low": _finite(comparison.ci_low),
        "ci_high": _finite(comparison.ci_high),
        "predicted": _finite(comparison.predicted),
        "reason": reason,
    }
