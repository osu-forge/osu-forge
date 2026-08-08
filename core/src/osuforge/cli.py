"""``forge`` — the command line.

There is deliberately no ``apply``, ``fix`` or ``write`` command, and there is no
code path anywhere in this package that opens an osu! file for writing. Settings
changes are printed as a diff and applied by the user in-game. osu! rewrites its
config on exit, so a third-party process editing it would lose the change
anyway — but the stronger reason is that "advisory only" is worth expressing as
a missing capability rather than a promise.

``--json`` goes to stdout and human output to stderr, so piping stays clean.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from osuforge import __version__
from osuforge.collect.epoch import ConfigEpoch
from osuforge.collect.journal import Journal, default_journal_path, scan
from osuforge.config import ConfigNotFoundError, OsuConfig, find_config
from osuforge.models import Basis, Finding, Severity
from osuforge.probes.base import ProbeResult
from osuforge.probes.collect import collect_probes
from osuforge.rules import ALL_RULES, CONFIG_ONLY_RULES, RuleContext, run_rules
from osuforge.rules.engine import Rule

__all__ = ["main"]

_SEVERITY_ORDER = (Severity.CRITICAL, Severity.WARN, Severity.INFO, Severity.PASS)

_BASIS_LABEL = {
    Basis.HARD_FACT: "fact",
    Basis.COMMUNITY_CONSENSUS: "consensus",
    Basis.PREFERENCE: "preference",
}

_ADVISORY_NOTE = "osu-forge never modifies osu! files. Apply anything above yourself, in-game."


def _wrap(text: str, width: int, indent: str) -> list[str]:
    import textwrap

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(
            textwrap.wrap(paragraph, width=width, initial_indent=indent, subsequent_indent=indent)
        )
    return lines


def _render(findings: Sequence[Finding], out: TextIO, *, verbose: bool) -> None:
    if not findings:
        print("No findings. Everything checked came back clean.", file=out)
        return

    width = 88
    for severity in _SEVERITY_ORDER:
        group = [f for f in findings if f.severity is severity]
        if not group:
            continue

        print(f"\n{severity.upper()}  ({len(group)})", file=out)
        print("-" * width, file=out)

        for finding in group:
            basis = _BASIS_LABEL[finding.basis]
            print(f"\n  {finding.title}  [{basis}]", file=out)
            for line in _wrap(finding.summary, width, "    "):
                print(line, file=out)

            if finding.has_change:
                print(
                    f"\n    {finding.current_value}  ->  {finding.recommended_value}",
                    file=out,
                )
            elif finding.current_value is not None:
                print(f"\n    current: {finding.current_value}", file=out)

            if verbose:
                if finding.rationale:
                    print(file=out)
                    for line in _wrap(finding.rationale, width, "    "):
                        print(line, file=out)
                for evidence in finding.evidence:
                    print(f"      - {evidence.label}: {evidence.value}", file=out)
                    print(f"        via {evidence.source}", file=out)

            for caveat in finding.caveats:
                for line in _wrap(f"caveat: {caveat}", width, "    "):
                    print(line, file=out)

            if finding.verification:
                for line in _wrap(f"check: {finding.verification}", width, "    "):
                    print(line, file=out)

            if finding.requires_elevation:
                print("    (this check needs administrator rights)", file=out)

    print(f"\n{_ADVISORY_NOTE}", file=out)
    if not verbose:
        print("Run with -v for the reasoning and the evidence behind each finding.", file=out)


def _to_json(findings: Sequence[Finding], config_path: Path) -> str:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "tool_version": __version__,
        "config": str(config_path),
        "advisory": True,
        "findings": [dataclasses.asdict(f) for f in findings],
    }
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


def _doctor(args: argparse.Namespace) -> int:
    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config = OsuConfig.from_path(config_path)

    # --no-probes means "config only", so the probe-dependent rules are not run
    # at all rather than run and then reported as unrunnable. Filling the output
    # with "could not check" for something the user opted out of would bury the
    # findings that did run.
    rules: tuple[Rule, ...]
    probes: Mapping[str, ProbeResult[object]]
    if args.no_probes:
        rules, probes = CONFIG_ONLY_RULES, {}
    else:
        rules = ALL_RULES
        probes = collect_probes(osu_exe=config_path.parent / "osu!.exe")

    findings = run_rules(rules, RuleContext(config=config, probes=probes))

    if args.only_facts:
        findings = [f for f in findings if f.basis is Basis.HARD_FACT]
    if args.severity:
        cutoff = _SEVERITY_ORDER.index(Severity(args.severity))
        findings = [f for f in findings if f.severity.rank <= cutoff]

    if args.json:
        print(_to_json(findings, config_path))
    else:
        print(f"osu-forge {__version__}", file=sys.stderr)
        print(f"config: {config_path}", file=sys.stderr)
        if args.no_probes:
            print("probes: skipped (--no-probes)", file=sys.stderr)
        _render(findings, sys.stderr, verbose=args.verbose)

    if args.fail_on:
        threshold = _SEVERITY_ORDER.index(Severity(args.fail_on))
        if any(f.severity.rank <= threshold for f in findings):
            return 1
    return 0


def _collect(args: argparse.Namespace) -> int:
    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config = OsuConfig.from_path(config_path)
    epoch = ConfigEpoch.of(config)
    replay_dir = args.replays or config_path.parent / "Data" / "r"
    if not replay_dir.is_dir():
        print(f"error: no replay folder at {replay_dir}", file=sys.stderr)
        return 2

    journal = Journal(args.journal or default_journal_path())
    result = scan(replay_dir, epoch, journal)

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "journal": str(journal.path),
                    "epoch": epoch.digest,
                    "epoch_changed": result.epoch_changed,
                    "settings": epoch.settings,
                    "added": [dataclasses.asdict(entry) for entry in result.added],
                    "already_known": result.already_known,
                    "unreadable": result.unreadable,
                },
                indent=2,
            )
        )
        return 0

    out = sys.stderr
    print(f"journal: {journal.path}", file=out)
    print(result.summary(), file=out)
    if result.epoch_changed:
        # The whole reason the fingerprint exists. A change means earlier
        # replays describe a different configuration, and pooling across it
        # would average two answers into one that is neither.
        print(
            "\nTiming-relevant settings changed since the last run. Replays before and "
            "after this point measure different configurations and are not pooled.",
            file=out,
        )
    for name, reason in list(result.unreadable.items())[:5]:
        print(f"  unreadable: {name}: {reason}", file=out)
    if not result.added:
        print("\nNothing new. Run this after playing; it is what accumulates the", file=out)
        print("sessions an offset estimate is actually limited by.", file=out)
    return 0


def _scan(args: argparse.Namespace) -> int:
    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config = OsuConfig.from_path(config_path)
    probes = collect_probes(osu_exe=config_path.parent / "osu!.exe")

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "config": str(config_path),
                    "entries": len(config.entries),
                    "redacted_keys": config.redacted_keys(),
                    "probes": {
                        pid: {
                            "status": str(result.status),
                            "source": result.source,
                            "reason": result.reason,
                        }
                        for pid, result in probes.items()
                    },
                },
                indent=2,
                default=str,
            )
        )
        return 0

    print(f"config: {config_path}  ({len(config.entries)} settings)", file=sys.stderr)
    if redacted := config.redacted_keys():
        print(f"redacted: {', '.join(redacted)}", file=sys.stderr)
    print("\nprobes:", file=sys.stderr)
    for pid, result in probes.items():
        detail = "" if result.ok else f"  ({result.reason})"
        print(f"  {pid:<34} {result.status}{detail}", file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Analysis and tuning for osu!stable. Recommends; never modifies.",
        epilog=_ADVISORY_NOTE,
    )
    parser.add_argument("--version", action="version", version=f"osu-forge {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None, help="path to osu!.<username>.cfg")
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")

    doctor = subparsers.add_parser(
        "doctor", parents=[common], help="check the config and system for problems"
    )
    doctor.add_argument(
        "-v", "--verbose", action="store_true", help="include reasoning and evidence"
    )
    doctor.add_argument(
        "--only-facts",
        action="store_true",
        help="hide findings based on community consensus or preference",
    )
    doctor.add_argument(
        "--severity",
        choices=[s.value for s in _SEVERITY_ORDER],
        help="only show findings at this severity or above",
    )
    doctor.add_argument(
        "--fail-on",
        choices=[s.value for s in _SEVERITY_ORDER],
        help="exit non-zero if any finding is at this severity or above",
    )
    doctor.add_argument(
        "--no-probes",
        action="store_true",
        help="config-only; skips every system measurement",
    )
    doctor.set_defaults(func=_doctor)

    scan = subparsers.add_parser(
        "scan", parents=[common], help="show what was found, without analysing it"
    )
    scan.set_defaults(func=_scan)

    collect = subparsers.add_parser(
        "collect",
        parents=[common],
        help="record new replays and the settings in force, so sessions accumulate",
        description=(
            "Appends any replay not already recorded, together with a fingerprint of "
            "the settings that affect timing. Run it after playing. An offset estimate "
            "is limited by how many separate sessions it has, not by how many hits, and "
            "sessions only accumulate forward. Reads nothing but files and does not stay "
            "running."
        ),
    )
    collect.add_argument(
        "--replays",
        type=Path,
        default=None,
        help="folder of .osr files (default: Data/r beside the config)",
    )
    collect.add_argument(
        "--journal",
        type=Path,
        default=None,
        help=f"where to append (default: {default_journal_path()})",
    )
    collect.set_defaults(func=_collect)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
