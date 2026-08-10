"""The corpus the server answers for, kept current as plays arrive.

:mod:`osuforge.analysis.corpus` answers "what keeps happening", but it answers
from entries, and an entry is a decision already made: which plays are believed,
which session each one belongs to, what its exclusion would be called. Someone
has to make those decisions the same way at startup and again every time the
watcher sees a play finish. This module is that someone, so the page's corpus
panel and the stderr line at startup can never disagree about what the corpus
says.

# What is decided here, and on what grounds

- **Belief.** A play whose simulation did not reproduce the game's own
  judgement counts still has hit errors, but they describe something other
  than what happened. Those plays are kept out of the entries and named in
  `excluded` with the screen's reason, and they still count against
  :class:`~osuforge.replay.validate.CorpusHealth` — a corpus that screens out
  a third of itself is not one to trust, however clean the survivors look.

- **Sessions.** Assigned from the replays' own timestamps with the same gap
  rule the estimator was validated against. Assigned fresh on every recompute,
  because a play arriving late can merge two sittings that looked separate.

- **The present.** When the collect journal has recorded a settings change,
  the verdict is computed from the plays under the settings now in force —
  the same refusal to pool across a change that `forge diagnose` makes. The
  older plays are not discarded: they become the before side of the progress
  view, where the two eras are estimated separately and only the difference
  crosses the boundary. Where the journal also recorded what the change was,
  that difference is scored against what the change predicted, so this panel
  carries the same contradicted verdict `forge diagnose` would print rather
  than a progress split that cannot come out wrong.

- **Local offsets.** Read once from `osu!.db` at startup and looked up per
  beatmap while the entries are built, rather than remembered with each play:
  the file holds the offset a map carries now, and a play analysed a month ago
  records nothing about what it was then. A map the player has nudged in game
  is judged on a shifted clock, so the inclusion policy drops its replays — and
  the answer carries whether the file could be read at all, because assuming
  zero and knowing zero are not the same claim.

- **The oracle.** Never claimed. Nothing in a `forge serve` run has compared
  these simulations object by object against an independent judge, so
  `oracle_agreement` is `None`, `may_recommend` is false, and the payload says
  so on every answer. The panel shows measurements; the step from a
  measurement to "change your offset" stays withheld until something with the
  authority to allow it has run.

# Recomputing costs seconds, and where that cost sits

The estimate inside :func:`~osuforge.analysis.corpus.diagnose` bootstraps ten
thousand resamples, which is seconds of one core on a real corpus. So the
answer is computed at moments that can afford it — startup, and a worker
thread after each new play — and :meth:`CorpusState.payload` only ever hands
back the last one. A request never waits on statistics, and the event loop
never runs them.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from osuforge.analysis.clustering import assign_sessions
from osuforge.analysis.corpus import Entry, beatmap_reading, by_beatmap, diagnose
from osuforge.analysis.gather import arrival_pairs
from osuforge.analysis.progress import fill_epochs, progress
from osuforge.analysis.verification import verify_boundary
from osuforge.replay.source import Judged
from osuforge.replay.validate import Agreement, CorpusHealth
from osuforge.server.protocol import corpus_payload

__all__ = ["CorpusState", "reduce_play"]


def reduce_play(judged: Judged) -> dict[str, Any]:
    """One judged play, reduced to the JSON-safe facts the corpus needs.

    This is the shape the analysis cache persists, which is why it is strings
    and numbers rather than the richer objects: what goes into the corpus at
    startup from the cache and what goes in live from the watcher must be the
    same reduction, or the corpus would quietly depend on how each play
    happened to arrive.

    `breaks` is the game's own miss count, matching what
    :func:`osuforge.analysis.gather.gather` records for the same replay.

    `arrival` is computed here and nowhere else: it needs the cursor frames and
    the modded beatmap, both of which are discarded once the play has been
    reduced. A play restored from the cache cannot grow an arrival split later,
    so a row written without one re-analyses rather than serving a corpus whose
    third axis is missing for half its history.
    """
    reported = judged.replay.judgements
    beatmap = judged.beatmap
    return {
        "beatmap_hash": judged.replay.beatmap_hash,
        "beatmap": f"{beatmap.artist} - {beatmap.title} [{beatmap.version}]",
        "played_at": judged.replay.timestamp.isoformat(),
        "errors": judged.simulation.timing_errors(),
        # No reported hits reads as fully missed, the same way `gather` reads
        # it: `select` then drops the replay, which is the safe direction for a
        # play whose judgement counts say nothing.
        "miss_rate": reported.count_miss / reported.total if reported.total else 1.0,
        "accuracy": reported.accuracy,
        "breaks": reported.count_miss,
        "agreement": str(judged.check.agreement),
        "agreement_reason": judged.check.reason,
        "fractional_windows": judged.simulation.fractional_windows,
        # `null` rather than NaN: this dictionary is persisted as strict JSON,
        # which has no spelling for one.
        "arrival": [
            [error, None if math.isnan(dwell) else dwell] for error, dwell in arrival_pairs(judged)
        ],
    }


@dataclass(frozen=True, slots=True)
class _Facts:
    """What one play contributes, fixed at the moment it was analysed."""

    beatmap_hash: str
    beatmap: str
    played_at: datetime
    errors: list[float]
    """Real milliseconds, early negative — already rate-corrected upstream."""

    miss_rate: float
    accuracy: float
    breaks: int
    agreement: Agreement
    agreement_reason: str
    fractional_windows: bool

    arrival: list[tuple[float, float]] = field(default_factory=list)
    """`(hit error, dwell)` pairs in real milliseconds, NaN dwell when unmeasured."""

    epoch: str | None = None
    """The settings fingerprint the collect journal recorded, if it has one.

    `None` for a play the journal has not seen — the ordinary state of a play
    made minutes ago, and of every play when no journal exists."""


class CorpusState:
    """Accumulates analysed plays and turns them into one served answer.

    `add` is cheap and safe to call from wherever a play was just analysed;
    `recompute` is the expensive step and deduplicates itself — recomputing a
    corpus that has not changed returns the previous answer without running
    the statistics again.

    `offsets` is the per-beatmap local offset map from `osu!.db`, as
    :attr:`osuforge.sources.osudb.Verified.offsets` returns it, and it is fixed
    for the life of the state — read once at startup, the way `forge diagnose`
    reads it. That is not only economy: the recompute key is the set of play
    names, so state arriving after a payload has been computed would never
    invalidate it. `None` means the file could not be read and zero is assumed,
    which is not the same claim as an empty dictionary's "read, and all zero".

    `epoch_settings` is the collect journal's fingerprint-to-settings map, as
    :meth:`osuforge.collect.journal.Journal.epoch_settings` returns it, and it
    is fixed for the same reason: a snapshot appended after startup does not
    change the set of play names, so it would never invalidate a computed
    payload anyway. A fingerprint missing from it was recorded before snapshots
    existed, and what it changed cannot be named.
    """

    def __init__(
        self,
        *,
        seed: int = 0,
        offsets: dict[str, int] | None = None,
        epoch_settings: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._seed = seed
        self._offsets = offsets
        self._epoch_settings = epoch_settings or {}
        self._facts: dict[str, _Facts] = {}
        self._lock = threading.Lock()
        self._computed_for: frozenset[str] | None = None
        self._payload: dict[str, Any] | None = None

    def __len__(self) -> int:
        return len(self._facts)

    def add(
        self,
        name: str,
        *,
        beatmap_hash: str,
        beatmap: str,
        played_at: datetime,
        errors: list[float],
        miss_rate: float,
        accuracy: float,
        breaks: int,
        agreement: Agreement,
        agreement_reason: str,
        fractional_windows: bool,
        arrival: list[tuple[float, float]] | None = None,
        epoch: str | None = None,
    ) -> None:
        """Record one analysed play. Re-adding a name replaces it."""
        self._facts[name] = _Facts(
            beatmap_hash=beatmap_hash,
            beatmap=beatmap,
            played_at=played_at,
            errors=errors,
            miss_rate=miss_rate,
            accuracy=accuracy,
            breaks=breaks,
            agreement=agreement,
            agreement_reason=agreement_reason,
            fractional_windows=fractional_windows,
            arrival=list(arrival or []),
            epoch=epoch,
        )

    def add_facts(self, name: str, facts: dict[str, Any], *, epoch: str | None = None) -> None:
        """Record a play from its JSON-safe reduction — the cache's shape.

        The inverse of :func:`reduce_play`. Raises `KeyError`, `ValueError` or
        `TypeError` on a dictionary that does not have that shape; a caller
        feeding from the cache treats that as a miss and re-analyses, because
        a malformed row must cost one recomputation rather than the corpus.
        """
        self.add(
            name,
            beatmap_hash=str(facts["beatmap_hash"]),
            beatmap=str(facts["beatmap"]),
            played_at=datetime.fromisoformat(str(facts["played_at"])),
            errors=[float(error) for error in facts["errors"]],
            miss_rate=float(facts["miss_rate"]),
            accuracy=float(facts["accuracy"]),
            breaks=int(facts["breaks"]),
            agreement=Agreement(str(facts["agreement"])),
            agreement_reason=str(facts["agreement_reason"]),
            fractional_windows=bool(facts["fractional_windows"]),
            # `null` came from a dwell that was never measured. Restoring it as
            # NaN keeps "not measured" distinct from "arrived at the moment of
            # the press", which is a measurement nobody made.
            arrival=[
                (float(error), math.nan if dwell is None else float(dwell))
                for error, dwell in facts["arrival"]
            ],
            epoch=epoch,
        )

    def payload(self) -> dict[str, Any] | None:
        """The last computed answer, instantly.

        `None` only before the first :meth:`recompute` — a state that has been
        recomputed at least once always has an answer, including the honest
        refusal of a corpus that is too thin.
        """
        return self._payload

    def recompute(self) -> dict[str, Any]:
        """Run the statistics over everything added so far. Seconds, not free.

        Serialised by a lock rather than sharded, because two recomputes racing
        would burn two cores to produce the same dictionary twice. A play added
        while one runs is not lost: it changes the key, so the refresh that
        follows it computes again rather than reusing this result.
        """
        with self._lock:
            facts = dict(self._facts)
            key = frozenset(facts)
            if key == self._computed_for and self._payload is not None:
                return self._payload
            fresh = self._build(facts)
            self._payload = fresh
            self._computed_for = key
            return fresh

    def _build(self, facts: dict[str, _Facts]) -> dict[str, Any]:
        believed = {
            name: fact for name, fact in facts.items() if fact.agreement is not Agreement.MISMATCH
        }
        unreproduced = {
            name: f"the simulation did not reproduce this play: {fact.agreement_reason}"
            for name, fact in facts.items()
            if fact.agreement is Agreement.MISMATCH
        }

        sessions = assign_sessions({name: fact.played_at for name, fact in believed.items()})
        ordered = sorted(
            (
                Entry(
                    replay=name,
                    beatmap_hash=fact.beatmap_hash,
                    beatmap=fact.beatmap,
                    played_at=fact.played_at,
                    session=sessions[name],
                    errors=fact.errors,
                    miss_rate=fact.miss_rate,
                    accuracy=fact.accuracy,
                    breaks=fact.breaks,
                    # Looked up now rather than remembered per play: `osu!.db`
                    # holds the offset a map carries today, and a copy taken
                    # when the play was analysed would go stale the moment the
                    # player nudges that map in game.
                    local_offset=(self._offsets or {}).get(fact.beatmap_hash, 0),
                    arrival=fact.arrival,
                )
                for name, fact in believed.items()
            ),
            key=lambda entry: entry.played_at,
        )

        # The verdict is about the settings now in force. Pooling across a
        # recorded change would average two configurations into one answer
        # that is neither — the same refusal `forge diagnose` makes — so
        # older-epoch plays leave the diagnosis and are named for it. They
        # are not discarded: they are the before side of the progress view.
        recorded = {name: fact.epoch for name, fact in believed.items() if fact.epoch is not None}
        filled = fill_epochs(ordered, recorded)
        current = filled[ordered[-1].replay] if ordered else None
        out_of_epoch: dict[str, str] = {}
        present: list[Entry] = []
        for entry in ordered:
            epoch = filled[entry.replay]
            if current is not None and epoch is not None and epoch != current:
                out_of_epoch[entry.replay] = (
                    f"played under settings {epoch}, not the current {current}"
                )
            else:
                present.append(entry)

        # Over everything believed, deliberately across epochs: the boundary is
        # the point of the view, and the sides are estimated separately rather
        # than pooled.
        across = progress(ordered, epochs=recorded, seed=self._seed)
        # A recommendation that is never checked against what happened next is
        # a recommendation that cannot be wrong. Scored here from the same
        # entries the split was drawn from, by the code `forge diagnose` calls,
        # so the panel and the command cannot reach different verdicts about
        # one journal. `None` on a midpoint boundary, which predicts nothing.
        verification = verify_boundary(ordered, across, self._epoch_settings, seed=self._seed)

        diagnosis = diagnose(present, seed=self._seed)
        per_beatmap = by_beatmap(present)
        health = CorpusHealth(
            total=len(facts),
            exact=sum(1 for fact in facts.values() if fact.agreement is Agreement.EXACT),
            close=sum(1 for fact in facts.values() if fact.agreement is Agreement.CLOSE),
            mismatched=len(unreproduced),
            fractional_window_replays=sum(1 for fact in facts.values() if fact.fractional_windows),
            oracle_agreement=None,
        )
        return corpus_payload(
            diagnosis,
            per_beatmap=per_beatmap,
            beatmaps_played=len({entry.beatmap_hash for entry in present}),
            health=health,
            unreproduced={**unreproduced, **out_of_epoch},
            local_offsets_known=self._offsets is not None,
            reading=beatmap_reading(diagnosis.bias, per_beatmap),
            progress=across,
            verification=verification,
        )
