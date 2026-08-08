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
        assert commands == {"doctor", "scan", "collect", "live", "profile"}
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
