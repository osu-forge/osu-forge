"""CLI behaviour.

Two of these matter more than the rest: that no credential reaches any output
stream, and that no command capable of writing to osu! exists. The second is
asserted against the parser rather than trusted, because "we would never add
that" is not a guarantee.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest

from osuforge.cli import _build_parser, main
from osuforge.collect.journal import default_journal_path

from .fixtures import REALISTIC_CFG, SENTINEL_PASSWORD, SENTINEL_TOKEN


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "osu!.tester.cfg"
    path.write_bytes(REALISTIC_CFG)
    return path


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestDoctor:
    def test_reports_the_known_findings(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = _run(["doctor", "--config", str(config_path), "--no-probes"], capsys)
        assert code == 0
        assert "keySkip" in err
        assert "CustomFrameLimit" in err

    def test_json_goes_to_stdout_and_humans_to_stderr(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # So `forge doctor --json | jq` works without the prose getting in.
        code, out, err = _run(
            ["doctor", "--config", str(config_path), "--no-probes", "--json"], capsys
        )
        assert code == 0
        payload = json.loads(out)
        assert payload["schema_version"] == 1
        assert payload["advisory"] is True
        assert payload["findings"]
        assert err == ""

    def test_only_facts_drops_consensus_and_preference(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, out, _ = _run(
            ["doctor", "--config", str(config_path), "--no-probes", "--json", "--only-facts"],
            capsys,
        )
        assert {f["basis"] for f in json.loads(out)["findings"]} == {"hard_fact"}

    def test_severity_filter_is_inclusive_of_higher_severities(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, out, _ = _run(
            ["doctor", "--config", str(config_path), "--no-probes", "--json", "--severity", "warn"],
            capsys,
        )
        severities = {f["severity"] for f in json.loads(out)["findings"]}
        assert severities <= {"critical", "warn"}
        assert "critical" in severities

    def test_fail_on_gives_a_non_zero_exit_for_scripting(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, _ = _run(
            ["doctor", "--config", str(config_path), "--no-probes", "--fail-on", "critical"],
            capsys,
        )
        assert code == 1

    def test_fail_on_is_quiet_when_nothing_reaches_the_threshold(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clean = tmp_path / "osu!.clean.cfg"
        clean.write_bytes(b"RawInput = 1\r\nMouseSpeed = 1\r\n")
        code, _, _ = _run(
            ["doctor", "--config", str(clean), "--no-probes", "--fail-on", "critical"], capsys
        )
        assert code == 0

    def test_no_probes_produces_no_skipped_findings(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # --no-probes means "config only", which must be a complete answer for
        # the config rules rather than a report full of "could not check".
        _, out, _ = _run(["doctor", "--config", str(config_path), "--no-probes", "--json"], capsys)
        ids = [f["id"] for f in json.loads(out)["findings"]]
        assert not [i for i in ids if i.endswith(".skipped")]

    def test_a_missing_config_exits_two_with_the_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = _run(["doctor", "--config", str(tmp_path / "nope.cfg")], capsys)
        assert code == 2
        assert "nope.cfg" in err

    def test_the_advisory_note_is_always_shown(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, _, err = _run(["doctor", "--config", str(config_path), "--no-probes"], capsys)
        assert "never modifies osu! files" in err


class TestScan:
    def test_lists_probes_and_redacted_keys(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = _run(["scan", "--config", str(config_path)], capsys)
        assert code == 0
        assert "Password" in err  # the key name, not the value
        assert "display.monitors" in err

    def test_json_shape(self, config_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(["scan", "--config", str(config_path), "--json"], capsys)
        payload = json.loads(out)
        assert payload["entries"] > 0
        assert set(payload["redacted_keys"]) == {"Password", "Token"}
        assert all("source" in p for p in payload["probes"].values())


class TestProfile:
    """The one place the tool asks rather than reads."""

    def run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], *args: str
    ) -> tuple[int, str, str]:
        return _run(["profile", "--path", str(tmp_path / "osu-forge.db"), *args], capsys)

    def test_an_empty_profile_leads_with_the_one_number_most_mice_have(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Most mice expose a single DPI and no cut-off setting at all. The
        # detailed form is for the ones that expose more, and asking everyone
        # for it makes a simple setup look like a misconfigured one.
        code, _, err = self.run(tmp_path, capsys)
        assert code == 0
        assert err.index("--dpi 800") < err.index("--dpi-x")
        assert "all most mice have" in err

    def test_a_single_dpi_is_not_reported_as_two_axes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, _, err = self.run(tmp_path, capsys, "--dpi", "800")
        assert "800 DPI" in err
        assert "horizontally" not in err, "one number should not be printed as two"
        assert "6.25%" in err

    def test_nothing_is_demanded_that_a_plain_mouse_cannot_answer(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An unset cut-off is a normal state, not an incomplete profile.
        _, _, err = self.run(tmp_path, capsys, "--dpi", "800")
        assert "no sensor cut-off" not in err

    def test_setting_both_axes_reports_the_step_on_each(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = self.run(tmp_path, capsys, "--dpi-x", "1350", "--dpi-y", "1400")
        assert code == 0
        assert "1350 x 1400 DPI" in err
        assert "3.70% horizontally, 3.57% vertically" in err

    def test_one_axis_alone_is_refused_rather_than_filled_in(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Copying the given axis onto the missing one would record isotropy
        # that was never claimed, and separate axes exist precisely because
        # assuming they match is the mistake.
        code, _, err = self.run(tmp_path, capsys, "--dpi-x", "1350")
        assert code == 2
        assert "must be given together" in err

    def test_dpi_and_a_single_axis_together_are_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = self.run(tmp_path, capsys, "--dpi", "800", "--dpi-y", "900")
        assert code == 2
        assert "sets both axes" in err

    def test_an_implausible_dpi_is_refused_before_it_is_written(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = self.run(tmp_path, capsys, "--dpi", "3")
        assert code == 2
        assert "typo rather than a mouse" in err
        # The store file itself is created on open; what must not survive is the
        # value. Checked by reading it back rather than by looking at the file.
        _, out, _ = self.run(tmp_path, capsys, "--json")
        assert json.loads(out)["mouse"] is None

    def test_a_cutoff_preset_expands_to_its_distances(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = self.run(tmp_path, capsys, "--asymmetric-cutoff", "3")
        assert code == 0
        assert "lift-off 3mm" in err and "landing 2mm" in err
        assert "recorded as though it were aim" in err, "3mm is past the line"

    def test_the_tightest_preset_gets_no_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, _, err = self.run(tmp_path, capsys, "--asymmetric-cutoff", "1")
        assert "recorded as though it were aim" not in err

    def test_the_two_declarations_accumulate_rather_than_replace(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.run(tmp_path, capsys, "--dpi", "800")
        self.run(tmp_path, capsys, "--asymmetric-cutoff", "1")
        code, out, _ = self.run(tmp_path, capsys, "--json")
        assert code == 0
        data = json.loads(out)
        assert data["mouse"]["dpi_x"] == 800, "setting the cut-off must not drop the mouse"
        assert data["tracking"]["lift_off_mm"] == 2.0

    def test_everything_written_is_marked_as_taken_on_trust(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, _, err = self.run(tmp_path, capsys, "--dpi", "800")
        assert "declared, not measured" in err

    def test_the_profile_never_lands_in_the_osu_install(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        monkeypatch.setenv("OSU_FORGE_PROFILE", str(tmp_path / "p.json"))
        code, _, err = _run(["profile", "--dpi", "800"], capsys)
        assert code == 0
        assert "osu!" not in err


class TestNoCredentialReachesAnyStream:
    @pytest.mark.parametrize(
        "argv",
        [
            ["doctor", "--no-probes"],
            ["doctor", "--no-probes", "-v"],
            ["doctor", "--no-probes", "--json"],
            ["scan"],
            ["scan", "--json"],
        ],
        ids=["doctor", "doctor-verbose", "doctor-json", "scan", "scan-json"],
    )
    def test_sentinels_appear_in_neither_stdout_nor_stderr(
        self, argv: list[str], config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, out, err = _run([*argv, "--config", str(config_path)], capsys)
        combined = out + err
        assert combined, "the command produced no output, so this proves nothing"
        for sentinel in (SENTINEL_PASSWORD, SENTINEL_TOKEN):
            assert sentinel not in combined


class TestAdvisoryOnly:
    def test_there_is_no_command_that_writes_osu_files(self) -> None:
        # "Advisory only" is expressed as a missing capability, so assert the
        # capability is actually missing rather than trusting a convention. The
        # exact set is deliberate: adding a command has to be a decision someone
        # made on purpose rather than something that slips in.
        parser = _build_parser()
        subparsers = next(
            action for action in parser._actions if hasattr(action, "choices") and action.choices
        )
        commands = set(subparsers.choices)  # type: ignore[arg-type]
        # `collect`, `live` and `profile` write, but only under
        # %LOCALAPPDATA%\osu-forge. Nothing here opens a file the game owns.
        assert commands == {
            "doctor",
            "scan",
            "collect",
            "diagnose",
            "live",
            "profile",
            "serve",
        }
        assert not commands & {"apply", "fix", "write", "set", "install", "repair"}

    def test_the_config_file_is_untouched_by_a_full_run(
        self, config_path: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        before = config_path.read_bytes()
        _run(["doctor", "--config", str(config_path)], capsys)
        _run(["scan", "--config", str(config_path)], capsys)

        # `collect` writes a journal, which makes it the one command that writes
        # anything at all. It must still leave everything belonging to osu!
        # exactly as it was.
        replays = tmp_path / "r"
        replays.mkdir()
        _run(
            [
                "collect",
                "--config",
                str(config_path),
                "--replays",
                str(replays),
                "--journal",
                str(tmp_path / "journal.jsonl"),
            ],
            capsys,
        )
        assert config_path.read_bytes() == before

    def test_the_journal_never_defaults_inside_the_osu_install(self) -> None:
        # The journal is the only file this tool creates. It belongs beside the
        # tool's own data, never in a directory the game owns and rewrites.
        assert "osu!" not in str(default_journal_path())

    def test_collect_leaks_no_credential(
        self, config_path: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # `collect` reads the configuration to fingerprint it, so it is a path a
        # credential could travel down that the other commands do not have.
        replays = tmp_path / "r"
        replays.mkdir()
        journal = tmp_path / "journal.jsonl"
        _, out, err = _run(
            [
                "collect",
                "--json",
                "--config",
                str(config_path),
                "--replays",
                str(replays),
                "--journal",
                str(journal),
            ],
            capsys,
        )
        combined = out + err + (journal.read_text("utf-8") if journal.exists() else "")
        assert combined, "the command produced no output, so this proves nothing"
        for sentinel in (SENTINEL_PASSWORD, SENTINEL_TOKEN):
            assert sentinel not in combined


class TestDiagnose:
    """The corpus command. The maps and replays come from the gather tests, so
    the bias asserted on here is one that was written into the files."""

    @pytest.fixture
    def folders(self, tmp_path: Path) -> tuple[Path, Path]:
        from datetime import UTC, datetime, timedelta

        from .test_analysis_gather import write_map, write_replay

        songs = tmp_path / "Songs"
        replays = tmp_path / "r"
        songs.mkdir()
        replays.mkdir()
        digest = write_map(songs, "map", title="Some Song")
        start = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for index in range(12):
            write_replay(
                replays,
                f"{index:02d}.osr",
                beatmap_hash=digest,
                when=start + timedelta(days=index // 3, minutes=20 * (index % 3)),
                errors=[2, 6, 10, 7, 5],
            )
        return songs, replays

    def _argv(self, config_path: Path, folders: tuple[Path, Path], *extra: str) -> list[str]:
        songs, replays = folders
        return [
            "diagnose",
            "--config",
            str(config_path),
            "--replays",
            str(replays),
            "--songs",
            str(songs),
            *extra,
        ]

    def test_a_corpus_of_plays_produces_an_answer(
        self, config_path: Path, folders: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = _run(self._argv(config_path, folders), capsys)
        assert code == 0
        assert "independent hits" in err
        assert "timing bias" in err
        assert "timing spread" in err
        # The design effect is the reason this command exists rather than a
        # mean over every hit, so it belongs in the output rather than only in
        # the JSON.
        assert "design effect" in err

    def test_json_carries_the_axes_and_their_actionability(
        self, config_path: Path, folders: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out, err = _run(self._argv(config_path, folders, "--json"), capsys)
        assert code == 0
        assert err == ""
        payload = json.loads(out)
        assert payload["schema_version"] == 1
        assert payload["usable"] is True
        assert payload["gathered"] == 12
        names = {axis["name"] for axis in payload["axes"]}
        assert {"timing bias", "timing spread"} <= names
        spread = next(a for a in payload["axes"] if a["name"] == "timing spread")
        assert spread["actionable"] is False, "no offset value narrows a distribution"

    def test_not_enough_data_exits_zero_and_says_what_is_missing(
        self, config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        songs = tmp_path / "Songs"
        replays = tmp_path / "r"
        songs.mkdir()
        replays.mkdir()
        code, out, _ = _run(self._argv(config_path, (songs, replays), "--json"), capsys)
        # Not an error. "Not enough data" and "nothing wrong" are opposite
        # conclusions, and a non-zero exit would file the first under the
        # second's absence.
        assert code == 0
        payload = json.loads(out)
        assert payload["usable"] is False
        assert "more replay(s)" in payload["insufficient"]

    def test_replays_from_other_settings_are_not_pooled(
        self, config_path: Path, folders: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json as _json

        replays = folders[1]
        journal = replays.parent / "journal.jsonl"
        names = sorted(path.name for path in replays.glob("*.osr"))
        with journal.open("w", encoding="utf-8", newline="\n") as handle:
            for index, name in enumerate(names):
                # The first three were played under an earlier configuration.
                # Pooling them would average two answers into one that is
                # neither, which is the whole reason the fingerprint exists.
                epoch = "old00000" if index < 3 else "new00000"
                handle.write(
                    _json.dumps(
                        {
                            "replay": name,
                            "beatmap_hash": "0" * 32,
                            "played_at": "2026-08-01T20:00:00+00:00",
                            "epoch": epoch,
                            "ruleset": 0,
                            "mods": 0,
                            "objects": 120,
                            "misses": 0,
                            "accuracy": 1.0,
                            "observed_at": "2026-08-01T21:00:00+00:00",
                        }
                    )
                    + "\n"
                )

        _, out, _ = _run(
            self._argv(config_path, folders, "--json", "--journal", str(journal)), capsys
        )
        payload = json.loads(out)
        assert payload["gathered"] == 9
        assert payload["epoch"] == "settings new00000"
        assert len(payload["skipped"]) == 3

        _, pooled, _ = _run(
            self._argv(config_path, folders, "--json", "--journal", str(journal), "--all-epochs"),
            capsys,
        )
        assert json.loads(pooled)["gathered"] == 12

    def test_local_offsets_are_reported_as_unknown_rather_than_zero(
        self, config_path: Path, folders: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        # There is no osu!.db in the sandbox, and "every map has no offset" is a
        # claim the tool has not earned by failing to read one.
        _, out, _ = _run(self._argv(config_path, folders, "--json"), capsys)
        assert json.loads(out)["local_offsets_known"] is False

    def portable_install(self, root: Path, *, plays: int = 0) -> Path:
        """An osu! install nowhere near %LOCALAPPDATA%, with an index in it.

        Everything `diagnose` reads by default lives here: the config, the
        Songs tree, Data/r, and osu!.db beside them. Returns the config path,
        which is the only thing the command is told.
        """
        from datetime import UTC, datetime, timedelta

        from .test_analysis_gather import write_map, write_replay
        from .test_sources_osudb import database, entry

        folder = "1234 Artist - Title"
        songs = root / "Songs" / folder
        replays = root / "Data" / "r"
        songs.mkdir(parents=True)
        replays.mkdir(parents=True)
        config = root / "osu!.tester.cfg"
        config.write_bytes(REALISTIC_CFG)

        digest = write_map(songs, "map", title="Some Song")
        start = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for index in range(plays):
            write_replay(
                replays,
                f"{index:02d}.osr",
                beatmap_hash=digest,
                when=start + timedelta(days=index // 3, minutes=20 * (index % 3)),
                errors=[2, 6, 10, 7, 5],
            )
        (root / "osu!.db").write_bytes(
            database([entry(md5=digest, local_offset=-12, folder=folder)])
        )
        return config

    def test_the_index_is_read_from_the_install_the_config_was_found_in(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # osu!stable is portable. The install turns up on any drive, and osu!.db
        # sits beside osu!.exe rather than under %LOCALAPPDATA% - so defaulting
        # the path the way the reader does on its own would find some other
        # install's index here, or none at all. That failure is silent: the
        # offsets go unread, every map is assumed to carry zero, and the report
        # blames the Songs folder, which was the one ingredient that was right.
        config = self.portable_install(tmp_path / "d_drive" / "osu!", plays=12)
        _, out, _ = _run(["diagnose", "--config", str(config), "--json"], capsys)
        payload = json.loads(out)
        assert payload["local_offsets_known"] is True
        # Read is not enough; the offset has to reach the inclusion policy.
        # Every replay here is of the map that carries it, so all twelve leave
        # the pooled estimate rather than being averaged in on a shifted clock.
        assert len(payload["dropped"]) == 12
        assert all("-12 ms" in reason for reason in payload["dropped"].values())

    def test_db_overrides_the_install_it_was_found_beside(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The default follows the install; --db still wins over it, including
        # when it names a file that is not there. Pointed at nothing, the answer
        # is "unknown" rather than the index sitting next to the config.
        config = self.portable_install(tmp_path / "d_drive" / "osu!")
        _, out, _ = _run(
            ["diagnose", "--config", str(config), "--json", "--db", str(tmp_path / "gone.db")],
            capsys,
        )
        assert json.loads(out)["local_offsets_known"] is False


class TestVerification:
    """The prediction, checked against what happened next.

    An offset recommendation predicts that the mean hit error moves the other
    way by the same amount. A tool that reports success whichever way the
    number actually moved launders a wrong recommendation into evidence for
    itself, so the contradicted case is asserted here beside the confirmed one.
    """

    BEFORE: ClassVar[list[int]] = [2, 6, 10, 7, 5]
    """Per-object error written into every replay before the change: +6 ms."""

    def corpus(self, root: Path, *, after: list[int]) -> tuple[Path, Path]:
        """Twelve plays over four evenings, six on each side of the change.

        Every evening carries its own 1 ms of drift, alternating so that each
        side's mean stays exactly where it was written. A corpus whose sessions
        all have identical means has no between-session variance at all, which
        is neither what replays look like nor what the clustered estimators are
        written for.
        """
        from datetime import UTC, datetime, timedelta

        from .test_analysis_gather import write_map, write_replay

        songs = root / "Songs"
        replays = root / "r"
        songs.mkdir()
        replays.mkdir()
        digest = write_map(songs, "map", title="Some Song")
        start = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for index in range(12):
            evening = index // 3
            drift = 1 if evening % 2 else -1
            write_replay(
                replays,
                f"{index:02d}.osr",
                beatmap_hash=digest,
                when=start + timedelta(days=evening, minutes=20 * (index % 3)),
                errors=[error + drift for error in (self.BEFORE if index < 6 else after)],
            )
        return songs, replays

    def journal(self, path: Path, replays: Path, *, snapshots: bool) -> Path:
        """Two eras on record: offset 0, then offset +5.

        Written through the same API `forge collect` writes it with, so the
        settings behind each fingerprint are recorded the way a real run
        records them — or, with `snapshots=False`, the way a journal from
        before snapshots existed looks.
        """
        from osuforge.collect.epoch import ConfigEpoch
        from osuforge.collect.journal import Entry, EpochSnapshot, Journal

        from .test_collect import config

        journal = Journal(path)
        names = sorted(found.name for found in replays.glob("*.osr"))
        for epoch, played in (
            (ConfigEpoch.of(config(Offset="0")), names[:6]),
            (ConfigEpoch.of(config(Offset="5")), names[6:]),
        ):
            if snapshots:
                journal.record_epoch(
                    EpochSnapshot(
                        digest=epoch.digest,
                        settings=epoch.settings,
                        observed_at="2026-08-01T19:00:00+00:00",
                    )
                )
            journal.append(
                [
                    Entry(
                        replay=name,
                        beatmap_hash="0" * 32,
                        played_at="2026-08-01T20:00:00+00:00",
                        epoch=epoch.digest,
                        ruleset=0,
                        mods=0,
                        objects=120,
                        misses=0,
                        accuracy=1.0,
                        observed_at="2026-08-01T21:00:00+00:00",
                    )
                    for name in played
                ]
            )
        return path

    def run(
        self,
        config_path: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        *,
        after: list[int],
        snapshots: bool = True,
    ) -> tuple[dict, str]:
        songs, replays = self.corpus(tmp_path, after=after)
        journal = self.journal(tmp_path / "journal.jsonl", replays, snapshots=snapshots)
        code, out, _ = _run(
            [
                "diagnose",
                "--config",
                str(config_path),
                "--replays",
                str(replays),
                "--songs",
                str(songs),
                "--journal",
                str(journal),
                "--all-epochs",
                "--json",
            ],
            capsys,
        )
        assert code == 0
        code, _, err = _run(
            [
                "diagnose",
                "--config",
                str(config_path),
                "--replays",
                str(replays),
                "--songs",
                str(songs),
                "--journal",
                str(journal),
                "--all-epochs",
            ],
            capsys,
        )
        assert code == 0
        return json.loads(out)["verification"], err

    def test_the_predicted_move_is_confirmed(
        self, config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Offset raised by 5, and the mean fell by 5 — which is what raising it
        # is supposed to do, since the game then judges objects later.
        verified, err = self.run(config_path, tmp_path, capsys, after=[-3, 1, 5, 2, 0])
        assert verified["verdict"] == "confirmed"
        assert verified["predicted"] == pytest.approx(-5.0)
        assert verified["difference"] == pytest.approx(-5.0, abs=0.5)
        assert verified["ci_high"] < 0.0
        assert "Verification: confirmed" in err

    def test_the_wrong_way_is_contradicted(
        self, config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The case this exists for. Same recorded change, mean went the other
        # way, and the honest answer is to put the change back.
        verified, err = self.run(config_path, tmp_path, capsys, after=[7, 11, 15, 12, 10])
        assert verified["verdict"] == "contradicted"
        assert verified["difference"] == pytest.approx(5.0, abs=0.5)
        assert "put it back" in verified["reason"]
        assert "Verification: contradicted" in err

    def test_a_journal_from_before_snapshots_says_what_it_cannot_know(
        self, config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The fingerprints differ, so something changed; without the values
        # behind them there is no way to say it was the offset. Reporting "no
        # offset change here" would be a claim the record does not support.
        verified, err = self.run(
            config_path, tmp_path, capsys, after=[-3, 1, 5, 2, 0], snapshots=False
        )
        assert verified["predicted"] is None
        assert verified["verdict"] != "confirmed"
        assert "was not recorded" in verified["reason"]
        assert "predates settings snapshots" in verified["reason"]
        # Wrapped to the terminal, so the sentence is read back unwrapped.
        assert "predates settings snapshots" in " ".join(err.split())

    def test_no_settings_change_means_no_verification(
        self, config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The middle of the sessions is a description of time. Scoring it
        # against a prediction nobody made is how a statistics screen starts
        # agreeing with itself.
        songs, replays = self.corpus(tmp_path, after=self.BEFORE)
        _, out, _ = _run(
            [
                "diagnose",
                "--config",
                str(config_path),
                "--replays",
                str(replays),
                "--songs",
                str(songs),
                "--journal",
                str(tmp_path / "absent.jsonl"),
                "--all-epochs",
                "--json",
            ],
            capsys,
        )
        payload = json.loads(out)
        assert payload["progress"]["boundary"]["kind"] == "midpoint"
        assert payload["verification"] is None


class TestParser:
    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "osu-forge" in capsys.readouterr().out

    def test_help_mentions_that_nothing_is_modified(self) -> None:
        buffer = io.StringIO()
        _build_parser().print_help(buffer)
        assert "never modifies" in buffer.getvalue()
