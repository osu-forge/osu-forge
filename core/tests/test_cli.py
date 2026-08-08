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
    def test_there_is_no_command_that_writes(self) -> None:
        # "Advisory only" is expressed as a missing capability, so assert the
        # capability is actually missing rather than trusting a convention.
        parser = _build_parser()
        subparsers = next(
            action for action in parser._actions if hasattr(action, "choices") and action.choices
        )
        commands = set(subparsers.choices)  # type: ignore[arg-type]
        assert commands == {"doctor", "scan"}
        assert not commands & {"apply", "fix", "write", "set", "install", "repair"}

    def test_the_config_file_is_untouched_by_a_full_run(
        self, config_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = config_path.read_bytes()
        _run(["doctor", "--config", str(config_path)], capsys)
        _run(["scan", "--config", str(config_path)], capsys)
        assert config_path.read_bytes() == before


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
