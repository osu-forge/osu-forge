"""Recording what was played, and the settings it was played under."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osuforge.collect.epoch import EPOCH_KEYS, ConfigEpoch, RedactedSettingError
from osuforge.collect.journal import (
    EPOCH_KIND,
    JOURNAL_ENV,
    Entry,
    EpochSnapshot,
    Journal,
    default_journal_path,
    scan,
)
from osuforge.config.parser import OsuConfig

from .replay_fixtures import build_replay


def replay_folder(tmp_path: Path, count: int = 2) -> Path:
    folder = tmp_path / "r"
    folder.mkdir()
    for index in range(count):
        data = build_replay(
            beatmap_hash=f"{index:032x}",
            timestamp=datetime(2026, 8, 8, 12, index, tzinfo=UTC),
            frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0), (10, 5.0, 5.0, 0)],
        )
        (folder / f"{index:032x}-1340000000000000{index:02d}.osr").write_bytes(data)
    return folder


def config(**overrides: str) -> OsuConfig:
    settings = {
        "Offset": "0",
        "AudioDevice": "Speakers",
        "AudioCompatibility": "0",
        "FrameSync": "2",
        "CustomFrameLimit": "240",
        "RefreshRate": "60",
        "OverrideRefreshRate": "0",
        "Fullscreen": "1",
        "Username": "someone",
    }
    settings.update(overrides)
    text = "\n".join(f"{key} = {value}" for key, value in settings.items()) + "\n"
    return OsuConfig.parse(text.encode())


class TestEpoch:
    def test_the_same_settings_give_the_same_fingerprint(self) -> None:
        assert ConfigEpoch.of(config()).digest == ConfigEpoch.of(config()).digest

    def test_a_changed_offset_changes_it(self) -> None:
        assert ConfigEpoch.of(config()).digest != ConfigEpoch.of(config(Offset="-12")).digest

    def test_an_unrelated_setting_does_not(self) -> None:
        # A key that does not move measured timing must not split the corpus.
        # Every split costs sessions that should have been pooled.
        assert ConfigEpoch.of(config()).digest == ConfigEpoch.of(config(Username="other")).digest

    @pytest.mark.parametrize("key", EPOCH_KEYS)
    def test_every_listed_setting_actually_affects_the_fingerprint(self, key: str) -> None:
        # A key in the list that the digest ignores would be a silent lie about
        # what is being controlled for.
        assert ConfigEpoch.of(config()).digest != ConfigEpoch.of(config(**{key: "999"})).digest

    def test_an_absent_setting_is_recorded_as_absent_not_dropped(self) -> None:
        without = OsuConfig.parse(b"Offset = 0\n")
        epoch = ConfigEpoch.of(without)
        assert epoch.settings["AudioDevice"] == ""
        # Adding it later has to count as a change.
        assert epoch.digest != ConfigEpoch.of(config()).digest

    def test_differences_name_what_changed(self) -> None:
        before = ConfigEpoch.of(config())
        after = ConfigEpoch.of(config(Offset="-8", FrameSync="0"))
        assert after.differences(before) == {"Offset": ("0", "-8"), "FrameSync": ("2", "0")}
        assert "Offset: 0 -> -8" in after.describe_change(before)

    def test_no_change_says_so(self) -> None:
        epoch = ConfigEpoch.of(config())
        assert epoch.describe_change(epoch) == "no timing-relevant setting changed"

    def test_a_redacted_setting_is_refused_rather_than_hashed(self, monkeypatch) -> None:
        # Hashing the placeholder would make every configuration look identical,
        # and the fingerprint would quietly stop working.
        monkeypatch.setattr("osuforge.collect.epoch.EPOCH_KEYS", ("Password",))
        with pytest.raises(RedactedSettingError, match="Password"):
            ConfigEpoch.of(OsuConfig.parse(b"Password = hunter2\n"))


class TestJournal:
    def test_new_replays_are_recorded_once(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path)
        journal = Journal(tmp_path / "journal.jsonl")
        epoch = ConfigEpoch.of(config())

        first = scan(folder, epoch, journal)
        assert len(first.added) == 2
        assert first.already_known == 0

        second = scan(folder, epoch, journal)
        assert second.added == []
        assert second.already_known == 2

    def test_the_path_is_not_recorded(self, tmp_path: Path) -> None:
        # It says where someone's osu! install lives and adds nothing an
        # analysis needs.
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        scan(folder, ConfigEpoch.of(config()), journal)
        text = journal.path.read_text("utf-8")
        assert str(folder) not in text
        # The settings snapshot leads, then the replay it applies to.
        assert json.loads(text.splitlines()[-1])["replay"].endswith(".osr")

    def test_a_settings_change_between_runs_is_flagged(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        scan(folder, ConfigEpoch.of(config()), journal)

        (folder / "later.osr").write_bytes(
            build_replay(frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)])
        )
        result = scan(folder, ConfigEpoch.of(config(Offset="-10")), journal)
        assert result.epoch_changed
        assert "settings changed" in result.summary()

    def test_no_change_is_not_flagged(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        epoch = ConfigEpoch.of(config())
        scan(folder, epoch, journal)
        assert not scan(folder, epoch, journal).epoch_changed

    def test_an_unreadable_replay_does_not_stop_the_rest(self, tmp_path: Path) -> None:
        # A zero-byte replay exists in the local corpus. The history is the
        # product here, so one bad file must not cost the others.
        folder = replay_folder(tmp_path, count=2)
        (folder / "broken.osr").write_bytes(b"")
        result = scan(folder, ConfigEpoch.of(config()), Journal(tmp_path / "journal.jsonl"))
        assert len(result.added) == 2
        assert "broken.osr" in result.unreadable

    def test_a_damaged_line_is_skipped_rather_than_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "journal.jsonl"
        good = Entry(
            replay="a.osr",
            beatmap_hash="0" * 32,
            played_at="2026-08-08T12:00:00+00:00",
            epoch="abc",
            ruleset=0,
            mods=0,
            objects=100,
            misses=1,
            accuracy=0.99,
            observed_at="2026-08-08T13:00:00+00:00",
        )
        Journal(path).append([good])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        assert [entry.replay for entry in Journal(path).entries()] == ["a.osr"]

    def test_appending_never_rewrites(self, tmp_path: Path) -> None:
        # The point of the record is that its past cannot change.
        path = tmp_path / "journal.jsonl"
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(path)
        scan(folder, ConfigEpoch.of(config()), journal)
        first = path.read_text("utf-8")

        (folder / "second.osr").write_bytes(
            build_replay(frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)])
        )
        scan(folder, ConfigEpoch.of(config(Offset="-5")), journal)
        assert path.read_text("utf-8").startswith(first)

    def test_an_empty_folder_records_nothing(self, tmp_path: Path) -> None:
        empty = tmp_path / "r"
        empty.mkdir()
        result = scan(empty, ConfigEpoch.of(config()), Journal(tmp_path / "journal.jsonl"))
        assert result.added == []
        assert not (tmp_path / "journal.jsonl").exists()


class TestEpochSnapshots:
    """The values behind a fingerprint, so a change can be named later.

    A digest says two configurations differ, not in what. `(old, new)` is the
    entire content of a prediction about what a change was supposed to do, and
    without these it is unrecoverable.
    """

    def test_the_first_scan_records_the_settings_behind_the_digest(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path)
        journal = Journal(tmp_path / "journal.jsonl")
        epoch = ConfigEpoch.of(config())

        scan(folder, epoch, journal)
        assert journal.epoch_settings() == {epoch.digest: epoch.settings}

    def test_the_settings_survive_the_round_trip(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path)
        journal = Journal(tmp_path / "journal.jsonl")
        epoch = ConfigEpoch.of(config(AudioDevice="Speakers (Realtek(R) Audio)"))

        scan(folder, epoch, journal)
        [snapshot] = journal.snapshots()
        assert snapshot.digest == epoch.digest
        assert snapshot.settings == epoch.settings
        # Every key that went into the digest, including the absent ones, or
        # the diff would report a setting appearing as no change at all.
        assert set(snapshot.settings) == set(EPOCH_KEYS)

    def test_a_changed_digest_records_a_second_snapshot(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        before = ConfigEpoch.of(config())
        scan(folder, before, journal)

        (folder / "later.osr").write_bytes(
            build_replay(frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)])
        )
        after = ConfigEpoch.of(config(Offset="-10"))
        scan(folder, after, journal)

        assert [snapshot.digest for snapshot in journal.snapshots()] == [
            before.digest,
            after.digest,
        ]
        recorded = journal.epoch_settings()
        assert after.differences(
            ConfigEpoch(digest=before.digest, settings=recorded[before.digest])
        ) == {"Offset": ("0", "-10")}

    def test_an_unchanged_rescan_records_nothing_more(self, tmp_path: Path) -> None:
        # Append-only means every needless line is permanent.
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        epoch = ConfigEpoch.of(config())
        scan(folder, epoch, journal)

        (folder / "later.osr").write_bytes(
            build_replay(frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)])
        )
        scan(folder, epoch, journal)
        scan(folder, epoch, journal)
        assert len(journal.snapshots()) == 1

    def test_a_journal_from_before_snapshots_still_reads(self, tmp_path: Path) -> None:
        # Fix-forward. Old entries keep their digest and lose nothing; what
        # changed across their boundaries reads as absent rather than as an
        # empty diff, which is a different claim.
        path = tmp_path / "journal.jsonl"
        Journal(path).append(
            [
                Entry(
                    replay="a.osr",
                    beatmap_hash="0" * 32,
                    played_at="2026-08-08T12:00:00+00:00",
                    epoch="abc",
                    ruleset=0,
                    mods=0,
                    objects=100,
                    misses=1,
                    accuracy=0.99,
                    observed_at="2026-08-08T13:00:00+00:00",
                )
            ]
        )
        assert [entry.replay for entry in Journal(path).entries()] == ["a.osr"]
        assert Journal(path).epoch_settings() == {}

    def test_a_snapshot_is_not_read_as_a_replay(self, tmp_path: Path) -> None:
        folder = replay_folder(tmp_path)
        journal = Journal(tmp_path / "journal.jsonl")
        scan(folder, ConfigEpoch.of(config()), journal)
        assert len(journal.entries()) == 2
        assert len(journal.known()) == 2

    def test_a_record_of_an_unknown_kind_is_skipped_rather_than_fatal(self, tmp_path: Path) -> None:
        # A journal written by a later version has to stay readable by this
        # one: the file is never rewritten, so a kind it refuses to parse
        # would cost the whole history rather than one line.
        folder = replay_folder(tmp_path, count=1)
        journal = Journal(tmp_path / "journal.jsonl")
        scan(folder, ConfigEpoch.of(config()), journal)
        with journal.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"kind": "something-later", "whatever": 1}) + "\n")

        assert len(journal.entries()) == 1
        assert len(journal.snapshots()) == 1

    def test_a_damaged_snapshot_costs_only_itself(self, tmp_path: Path) -> None:
        path = tmp_path / "journal.jsonl"
        journal = Journal(path)
        journal.record_epoch(
            EpochSnapshot(
                digest="1111111111111111",
                settings={"Offset": "0"},
                observed_at="2026-08-08T13:00:00+00:00",
            )
        )
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"kind": EPOCH_KIND, "digest": "2222222222222222"}) + "\n")

        assert journal.epoch_settings() == {"1111111111111111": {"Offset": "0"}}


class TestJournalPath:
    def test_the_environment_overrides_the_default(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv(JOURNAL_ENV, str(tmp_path / "elsewhere.jsonl"))
        assert default_journal_path() == tmp_path / "elsewhere.jsonl"

    def test_the_default_is_outside_the_osu_install(self, monkeypatch) -> None:
        # Nothing this tool writes belongs in a directory the game owns.
        monkeypatch.delenv(JOURNAL_ENV, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        path = default_journal_path()
        assert path.parts[-2:] == ("osu-forge", "journal.jsonl")
        assert "osu!" not in str(path)
