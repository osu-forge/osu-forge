"""An append-only record of what has been played and under what settings.

One line of JSON per replay, written once and never rewritten. Append-only
because the point is to accumulate a history that later analysis can trust: a
file that gets rewritten is a file whose past can quietly change, and the whole
reason this exists is to make "the offset was X when these were played" a fact
rather than a recollection.

Nothing here reads memory and nothing runs in the background. `forge collect`
scans, appends what is new, and exits.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from osuforge.collect.epoch import ConfigEpoch
from osuforge.replay.parse import ReplayParseError, parse_path

__all__ = ["JOURNAL_ENV", "Entry", "Journal", "ScanResult", "default_journal_path", "scan"]

JOURNAL_ENV = "OSU_FORGE_JOURNAL"
"""Environment variable overriding where the journal is written."""


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
    def from_json(cls, line: str) -> Entry:
        data = json.loads(line)
        return cls(**{field: data[field] for field in cls.__slots__})


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

    def entries(self) -> list[Entry]:
        if not self.path.exists():
            return []
        found: list[Entry] = []
        for number, line in enumerate(self.path.read_text("utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                found.append(Entry.from_json(line))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                # A damaged line is skipped rather than fatal. Losing one record
                # is better than a corrupted file making every later run fail,
                # and appending nothing would lose the rest of the history too.
                print(f"{self.path}:{number}: skipping unreadable entry ({exc})")
        return found

    def known(self) -> set[str]:
        return {entry.replay for entry in self.entries()}

    def append(self, entries: list[Entry]) -> None:
        if not entries:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for entry in entries:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


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

    journal.append(added)
    return ScanResult(
        added=added,
        already_known=len(known),
        unreadable=unreadable,
        epoch=epoch,
        previous_epoch=previous_epoch,
    )
