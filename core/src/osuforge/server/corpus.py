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

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from osuforge.analysis.clustering import assign_sessions
from osuforge.analysis.corpus import Entry, beatmap_reading, by_beatmap, diagnose
from osuforge.replay.validate import Agreement, CorpusHealth
from osuforge.server.protocol import corpus_payload

__all__ = ["CorpusState"]


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


class CorpusState:
    """Accumulates analysed plays and turns them into one served answer.

    `add` is cheap and safe to call from wherever a play was just analysed;
    `recompute` is the expensive step and deduplicates itself — recomputing a
    corpus that has not changed returns the previous answer without running
    the statistics again.
    """

    def __init__(self, *, seed: int = 0) -> None:
        self._seed = seed
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
        entries = [
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
            )
            for name, fact in believed.items()
        ]

        diagnosis = diagnose(entries, seed=self._seed)
        per_beatmap = by_beatmap(entries)
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
            beatmaps_played=len({entry.beatmap_hash for entry in entries}),
            health=health,
            unreproduced=unreproduced,
            reading=beatmap_reading(diagnosis.bias, per_beatmap),
        )
