"""An append-only record of what has been played and under what settings.

One line of JSON per record, written once and never rewritten. Append-only
because the point is to accumulate a history that later analysis can trust: a
file that gets rewritten is a file whose past can quietly change, and the whole
reason this exists is to make "the offset was X when these were played" a fact
rather than a recollection.

Two kinds of record. A replay line carries no `kind` field at all, which is
what keeps every line already on disk readable; anything else carries one, and
a reader that meets a kind it does not know skips it rather than failing. The
format can therefore grow without stranding the journals that exist.

The second kind is the settings snapshot. A digest says two configurations
differ but not in what, and `(old, new)` is the entire content of a prediction
about what a change was supposed to do — so the values behind each fingerprint
are written down as they come into force. Fix-forward only: entries recorded
before snapshots existed keep their digest and lose nothing, but what changed
across those boundaries is gone and readers have to say so rather than report
an empty diff.

Nothing here reads memory and nothing runs in the background. `forge collect`
scans, appends what is new, and exits.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from osuforge.collect.epoch import ConfigEpoch
from osuforge.replay.parse import ReplayParseError, parse_path

__all__ = [
    "EPOCH_KIND",
    "JOURNAL_ENV",
    "Entry",
    "EpochSnapshot",
    "Journal",
    "ScanResult",
    "default_journal_path",
    "scan",
]

JOURNAL_ENV = "OSU_FORGE_JOURNAL"
"""Environment variable overriding where the journal is written."""

EPOCH_KIND = "epoch"
"""Value of the `kind` field on a settings snapshot line."""

_KIND = "kind"
"""The field that says what a line is. Absent on replay entries, which is the
compatibility guarantee: lines written before this existed still parse."""


def default_journal_path() -> Path:
    """`%LOCALAPPDATA%\\osu-forge\\journal.jsonl`, or the environment override.

    Outside the osu! install on purpose. Nothing this tool writes belongs in a
    directory the game owns.
    """
    override = os.environ.get(JOURNAL_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "journal.jsonl"


@dataclass(frozen=True, slots=True)
class Entry:
    """One replay, as it was when it was first seen."""

    replay: str
    """File name only. The path is not recorded — it says where the user's osu!
    install lives and adds nothing an analysis needs."""

    beatmap_hash: str
    played_at: str
    """ISO-8601, UTC. From the replay header rather than the file's mtime, which
    a backup or a sync would rewrite."""

    epoch: str
    """The configuration fingerprint at the time this replay was first seen.

    Not at the time it was played — nothing records that. A replay observed long
    after the fact is attributed to the settings in force when it was found,
    which is why running `forge collect` regularly is what makes the record
    accurate.
    """

    ruleset: int
    mods: int
    objects: int
    misses: int
    accuracy: float
    observed_at: str

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Entry:
        return cls(**{field: data[field] for field in cls.__slots__})


@dataclass(frozen=True, slots=True)
class EpochSnapshot:
    """The settings behind one fingerprint, as they were when it came into force.

    Keyed by the digest rather than by time, because the digest is a hash of
    exactly these values: a snapshot recorded now is equally true of every
    entry that carries the same digest, whenever it was written.
    """

    digest: str
    settings: dict[str, str]
    observed_at: str

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> EpochSnapshot:
        settings = data["settings"]
        if not isinstance(settings, dict):
            raise TypeError("settings is not an object")
        return cls(
            digest=str(data["digest"]),
            settings={str(key): str(value) for key, value in settings.items()},
            observed_at=str(data["observed_at"]),
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    added: list[Entry]
    already_known: int
    unreadable: dict[str, str]
    epoch: ConfigEpoch
    previous_epoch: str | None
    """The fingerprint on the most recent existing entry, if there was one."""

    @property
    def epoch_changed(self) -> bool:
        return self.previous_epoch is not None and self.previous_epoch != self.epoch.digest

    def summary(self) -> str:
        parts = [
            f"{len(self.added)} new replay(s), {self.already_known} already recorded",
            f"configuration {self.epoch.digest}",
        ]
        if self.epoch_changed:
            parts.append("settings changed since the last run")
        if self.unreadable:
            parts.append(f"{len(self.unreadable)} unreadable")
        return "; ".join(parts)


class Journal:
    """The append-only file, and the small amount of reading it needs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _records(self) -> Iterator[tuple[int, dict[str, Any]]]:
        """Every line that parses as an object, with its line number.

        One reader for all kinds. Which kind a line is stays the caller's
        decision, so a kind added after this version was written is skipped by
        it rather than being fatal to the whole file.
        """
        if not self.path.exists():
            return
        for number, line in enumerate(self.path.read_text("utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                # A damaged line is skipped rather than fatal. Losing one record
                # is better than a corrupted file making every later run fail,
                # and appending nothing would lose the rest of the history too.
                print(f"{self.path}:{number}: skipping unreadable entry ({exc})")
                continue
            if not isinstance(data, dict):
                print(f"{self.path}:{number}: skipping unreadable entry (not an object)")
                continue
            yield number, data

    def entries(self) -> list[Entry]:
        found: list[Entry] = []
        for number, data in self._records():
            if data.get(_KIND) is not None:
                continue
            try:
                found.append(Entry.from_data(data))
            except (KeyError, TypeError) as exc:
                print(f"{self.path}:{number}: skipping unreadable entry ({exc})")
        return found

    def known(self) -> set[str]:
        return {entry.replay for entry in self.entries()}

    def snapshots(self) -> list[EpochSnapshot]:
        """The recorded settings behind the fingerprints, oldest first."""
        found: list[EpochSnapshot] = []
        for number, data in self._records():
            if data.get(_KIND) != EPOCH_KIND:
                continue
            try:
                found.append(EpochSnapshot.from_data(data))
            except (KeyError, TypeError) as exc:
                print(f"{self.path}:{number}: skipping unreadable snapshot ({exc})")
        return found

    def epoch_settings(self) -> dict[str, dict[str, str]]:
        """Fingerprint to the settings behind it, for naming what changed.

        A digest recorded before snapshots existed is absent here rather than
        empty: "nothing changed" and "what changed was never written down" are
        different answers, and only one of them supports a prediction.
        """
        return {snapshot.digest: snapshot.settings for snapshot in self.snapshots()}

    def append(self, entries: list[Entry]) -> None:
        if not entries:
            return
        self._write([asdict(entry) for entry in entries])

    def record_epoch(self, snapshot: EpochSnapshot) -> None:
        self._write([{_KIND: EPOCH_KIND, **asdict(snapshot)}])

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def scan(
    replay_dir: Path,
    epoch: ConfigEpoch,
    journal: Journal,
    *,
    now: datetime | None = None,
) -> ScanResult:
    """Record every replay in `replay_dir` that is not already in the journal."""
    existing = journal.entries()
    known = {entry.replay for entry in existing}
    previous_epoch = existing[-1].epoch if existing else None
    observed_at = (now or datetime.now(UTC)).isoformat()

    added: list[Entry] = []
    unreadable: dict[str, str] = {}
    for path in sorted(replay_dir.glob("*.osr")):
        if path.name in known:
            continue
        try:
            replay = parse_path(path)
        except (ReplayParseError, OSError) as exc:
            # Recorded rather than raised. A zero-byte replay exists in the
            # local corpus, and one bad file must not stop the rest being
            # recorded — the history is the product here.
            unreadable[path.name] = str(exc)
            continue
        added.append(
            Entry(
                replay=path.name,
                beatmap_hash=replay.beatmap_hash,
                played_at=replay.timestamp.isoformat(),
                epoch=epoch.digest,
                ruleset=int(replay.ruleset),
                mods=int(replay.mods),
                objects=replay.judgements.total,
                misses=replay.judgements.count_miss,
                accuracy=round(replay.judgements.accuracy, 6),
                observed_at=observed_at,
            )
        )

    if added:
        # Written only alongside replays, and only when these values are not
        # already the ones on file. Gated on both because a fingerprint nothing
        # was played under cannot be the boundary of any comparison, and a
        # rescan that changed nothing must not grow the file. What this buys is
        # the invariant the comparison needs: every entry appended from here on
        # has the settings behind its digest recorded at or before it.
        recorded = journal.snapshots()
        if not recorded or recorded[-1].digest != epoch.digest:
            journal.record_epoch(
                EpochSnapshot(
                    digest=epoch.digest,
                    settings=dict(epoch.settings),
                    observed_at=observed_at,
                )
            )

    journal.append(added)
    return ScanResult(
        added=added,
        already_known=len(known),
        unreadable=unreadable,
        epoch=epoch,
        previous_epoch=previous_epoch,
    )
