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
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from osuforge import __version__, hardware
from osuforge.collect.epoch import ConfigEpoch
from osuforge.collect.journal import Journal, default_journal_path, scan
from osuforge.config import ConfigNotFoundError, OsuConfig, find_config
from osuforge.live.render import REFRESH_SECONDS, render
from osuforge.live.watch import POLL_SECONDS, BeatmapIndex, Session, analyse, default_page_path
from osuforge.models import Basis, Finding, Severity
from osuforge.probes.base import ProbeResult
from osuforge.probes.collect import collect_probes
from osuforge.rules import ALL_RULES, CONFIG_ONLY_RULES, RuleContext, run_rules
from osuforge.rules.engine import Rule
from osuforge.server.security import DEFAULT_PORT
from osuforge.store import StoreError, default_db_path, open_store
from osuforge.store import profile as store_profile

__all__ = ["main"]

_SEVERITY_ORDER = (Severity.CRITICAL, Severity.WARN, Severity.INFO, Severity.PASS)

_BASIS_LABEL = {
    Basis.HARD_FACT: "fact",
    Basis.COMMUNITY_CONSENSUS: "consensus",
    Basis.PREFERENCE: "preference",
}

_ADVISORY_NOTE = "osu-forge never modifies osu! files. Apply anything above yourself, in-game."


def _finite(value: float) -> float | None:
    """`None` for a NaN, so JSON carries "not measured" rather than a token
    only some parsers accept and none of them agree about."""
    import math

    return value if math.isfinite(value) else None


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


def _live(args: argparse.Namespace) -> int:
    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    install = config_path.parent
    replay_dir = args.replays or install / "Data" / "r"
    songs_dir = args.songs or install / "Songs"
    for label, folder in (("replay", replay_dir), ("songs", songs_dir)):
        if not folder.is_dir():
            print(f"error: no {label} folder at {folder}", file=sys.stderr)
            return 2

    page = args.page or default_page_path()
    page.parent.mkdir(parents=True, exist_ok=True)

    index = BeatmapIndex(songs_dir)
    session = Session()
    existing = sorted(replay_dir.glob("*.osr"), key=lambda path: path.stat().st_mtime)
    seen = {path.name for path in existing}

    print(f"page:    {page}", file=sys.stderr)
    print(f"beatmaps indexed: {len(index)}", file=sys.stderr)

    # The page opens with recent plays already on it. A first version started
    # empty, on the reasoning that a page should fill up as you play rather than
    # open on a wall of history — which quietly threw away every play the user
    # had already made and looked, reasonably, like the tool had lost them.
    recent = existing[-args.history :] if args.history > 0 else []
    if recent:
        print(f"reading the last {len(recent)} play(s)...", file=sys.stderr)
        for path in recent:
            result = analyse(path, index)
            if isinstance(result, str):
                session.skipped[path.name] = result
            else:
                session.add(result)

    page.write_text(render(session), encoding="utf-8")
    print(f"open it in a browser; it reloads itself every {REFRESH_SECONDS}s", file=sys.stderr)
    print("Ctrl-C to stop. Nothing is left running afterwards.", file=sys.stderr)

    try:
        while True:
            fresh = sorted(path for path in replay_dir.glob("*.osr") if path.name not in seen)
            for path in fresh:
                seen.add(path.name)
                # osu! writes the file as the results screen appears; a moment's
                # wait avoids reading one that is still being written, and a
                # failure here is reported rather than retried forever.
                time.sleep(0.5)
                result = analyse(path, index)
                if isinstance(result, str):
                    session.skipped[path.name] = result
                    print(f"  skipped {path.name}: {result}", file=sys.stderr)
                else:
                    session.add(result)
                    print(
                        f"  {result.artist} - {result.title} [{result.version}]: "
                        f"{result.accuracy:.2%}, mean {result.mean_error:+.1f} ms",
                        file=sys.stderr,
                    )
            if fresh:
                page.write_text(render(session), encoding="utf-8")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
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


def _epoch_filter(
    paths: list[Path], journal: Journal, *, pool: bool
) -> tuple[list[Path], str, dict[str, str]]:
    """Keep only the replays played under the settings now in force.

    An offset measurement is about the configuration it was measured under, so
    pooling across a change to one averages two different answers into one that
    is neither. The journal is what knows when settings changed; without one,
    everything is pooled and the caller is told that is what happened.

    Returns the surviving paths, a sentence about which epoch they belong to,
    and the file names left out with why.
    """
    if pool:
        return (
            paths,
            "pooling every replay regardless of settings (--all-epochs)",
            {},
        )

    recorded = {entry.replay: entry.epoch for entry in journal.entries()}
    if not recorded:
        return (
            paths,
            f"no journal at {journal.path}, so every replay is pooled and a settings "
            "change since the oldest one would be averaged in unnoticed — run "
            "`forge collect` after playing to record them",
            {},
        )

    # The most recently recorded epoch is the one now in force. The journal is
    # append-only and a dict preserves insertion order, so the last value read
    # is the newest fingerprint seen.
    current = list(recorded.values())[-1]

    kept: list[Path] = []
    dropped: dict[str, str] = {}
    for path in paths:
        epoch = recorded.get(path.name)
        if epoch is None:
            dropped[path.name] = "not in the journal; run `forge collect` to record it"
        elif epoch != current:
            dropped[path.name] = f"played under settings {epoch}, not the current {current}"
        else:
            kept.append(path)
    return kept, f"settings {current}", dropped


def _diagnose(args: argparse.Namespace) -> int:
    """What the corpus supports, rather than what one play happened to do."""
    from osuforge.analysis.corpus import by_beatmap, diagnose
    from osuforge.analysis.gather import gather
    from osuforge.analysis.progress import progress
    from osuforge.sources import osudb

    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    install = config_path.parent
    replay_dir = args.replays or install / "Data" / "r"
    songs_dir = args.songs or install / "Songs"
    for label, folder in (("replay", replay_dir), ("songs", songs_dir)):
        if not folder.is_dir():
            print(f"error: no {label} folder at {folder}", file=sys.stderr)
            return 2

    found = sorted(replay_dir.glob("*.osr"))
    journal = Journal(args.journal or default_journal_path())
    paths, epoch_note, out_of_epoch = _epoch_filter(found, journal, pool=args.all_epochs)

    # Read rather than assumed. A map the player nudged in game is judged on a
    # shifted clock, and its hit errors are not on the same scale as the rest —
    # but a file that cannot be read has to mean "unknown", not "all zero".
    verified, offsets_finding = osudb.load(path=args.db, songs=songs_dir)
    offsets = verified.offsets if verified is not None and verified.trustworthy else None

    collected = gather(paths, BeatmapIndex(songs_dir), offsets=offsets)
    result = diagnose(collected.entries, decomposition=collected.decomposition)
    maps = by_beatmap(collected.entries) if result.usable else {}

    # Only when the corpus spans every epoch: the point of the split is the
    # settings change, and the default run has already filtered one era out.
    across = None
    if args.all_epochs:
        recorded = {entry.replay: entry.epoch for entry in journal.entries()}
        across = progress(collected.entries, epochs=recorded)

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "replays": str(replay_dir),
                    "epoch": epoch_note,
                    "considered": len(found),
                    "gathered": len(collected.entries),
                    "skipped": collected.skipped | out_of_epoch,
                    "local_offsets_known": collected.offsets_known,
                    "usable": result.usable,
                    "insufficient": result.insufficient,
                    "summary": result.summary(),
                    "hits": result.hits,
                    "sessions": result.sessions,
                    "effective_hits": _finite(result.effective_hits),
                    "bias": (
                        None
                        if result.bias is None
                        else {
                            "mean": result.bias.mean,
                            "ci_low": result.bias.ci_low,
                            "ci_high": result.bias.ci_high,
                            "ci_source": result.bias.ci_source,
                            "design_effect": _finite(result.bias.design_effect),
                            "icc": result.bias.icc,
                        }
                    ),
                    "unstable_rate": (
                        None if result.timing is None else _finite(result.timing.unstable_rate)
                    ),
                    "axes": [
                        {
                            "name": axis.name,
                            "verdict": axis.verdict,
                            "actionable": axis.actionable,
                            "detail": axis.detail,
                            "evidence": axis.evidence,
                        }
                        for axis in result.axes
                    ],
                    "dropped": result.dropped,
                    "by_beatmap": {
                        name: {
                            "mean": mean.mean,
                            "ci_low": mean.ci_low,
                            "ci_high": mean.ci_high,
                            "replays": mean.n_replays,
                            "hits": mean.n_hits,
                        }
                        for name, mean in maps.items()
                    },
                    "progress": (
                        None
                        if across is None
                        else {
                            "boundary": (
                                None
                                if across.boundary_kind is None or across.boundary_at is None
                                else {
                                    "kind": across.boundary_kind,
                                    "at": across.boundary_at.isoformat(),
                                    "label": across.boundary_label,
                                }
                            ),
                            "insufficient": across.insufficient,
                            "shift": (
                                None
                                if across.shift is None
                                else {
                                    "before_mean": across.shift.before.mean,
                                    "after_mean": across.shift.after.mean,
                                    "difference": across.shift.difference,
                                    "ci_low": across.shift.ci_low,
                                    "ci_high": across.shift.ci_high,
                                    "ci_source": across.shift.ci_source,
                                    "spread_before": _finite(across.shift.spread_before),
                                    "spread_after": _finite(across.shift.spread_after),
                                    "moved": across.shift.moved,
                                    "verdict": across.shift.verdict(),
                                }
                            ),
                        }
                    ),
                },
                indent=2,
            )
        )
        return 0

    out = sys.stderr
    print(f"osu-forge {__version__}", file=out)
    print(f"replays:  {replay_dir}", file=out)
    print(f"epoch:    {epoch_note}", file=out)
    print(collected.summary(), file=out)
    if out_of_epoch:
        print(f"{len(out_of_epoch)} replay(s) left out by the settings filter", file=out)
        if any("played under settings" in reason for reason in out_of_epoch.values()):
            print(
                "Settings changed inside this corpus. Run with --all-epochs to see "
                "what moved across the change.",
                file=out,
            )
    if not collected.offsets_known:
        print(f"  {offsets_finding.title}", file=out)

    print(f"\n{result.summary()}\n", file=out)
    if not result.usable:
        # Not an error. "Not enough data" and "nothing wrong" are opposite
        # conclusions and only one of them is a reason to change something.
        return 0

    for axis in result.axes:
        flag = "[actionable]" if axis.actionable else "[          ]"
        print(f"{flag}  {axis.name:<14} {axis.verdict}", file=out)
        for line in _wrap(axis.detail, 78, " " * 16):
            print(line, file=out)
        for evidence in axis.evidence:
            for line in _wrap(evidence, 78, " " * 16):
                print(line, file=out)
        print(file=out)

    if maps:
        print("Per beatmap, where there are enough replays to say anything:", file=out)
        for name, mean in sorted(maps.items(), key=lambda item: -abs(item[1].mean)):
            marker = " *" if mean.excludes_zero else "  "
            print(
                f" {marker} {name[:52]:<52} {mean.mean:+6.1f} ms "
                f"({mean.ci_low:+.1f} to {mean.ci_high:+.1f}, {mean.n_replays} replays)",
                file=out,
            )
        print(
            "\n* marks an interval that excludes zero. A weakness on one map is a map to "
            "practise; one that follows you everywhere is a habit, and a pooled interval "
            "that excludes zero while no individual map's does is what a global offset "
            "looks like.",
            file=out,
        )

    if across is not None and across.boundary_kind is not None:
        print(f"\nAcross {across.boundary_label}:", file=out)
        if across.shift is not None:
            shift = across.shift
            for side, estimated in (("before", shift.before), ("after ", shift.after)):
                print(
                    f"  {side}  {estimated.mean:+6.1f} ms "
                    f"({estimated.ci_low:+.1f} to {estimated.ci_high:+.1f}, "
                    f"{estimated.n_replays} replays in {estimated.n_sessions} sessions)",
                    file=out,
                )
            for line in _wrap(shift.verdict(), 78, "  "):
                print(line, file=out)
            print(
                f"  spread {shift.spread_before:.1f} -> {shift.spread_after:.1f} ms — a "
                "description; no interval is claimed on this difference",
                file=out,
            )
        elif across.insufficient:
            for line in _wrap(across.insufficient, 78, "  "):
                print(line, file=out)

    for name, reason in list(result.dropped.items())[:5]:
        print(f"  excluded: {name}: {reason}", file=out)

    print(f"\n{_ADVISORY_NOTE}", file=out)
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


def _serve(args: argparse.Namespace) -> int:
    """Read the site and the replays, then hand both to the server and block."""
    import asyncio

    import osu_forge_diffcalc as diffcalc

    from osuforge.live.watch import BeatmapIndex, analyse
    from osuforge.replay.parse import ReplayParseError, parse_path
    from osuforge.replay.simulate import simulate
    from osuforge.server import (
        Broadcaster,
        CorpusState,
        ReplayPayload,
        ServerError,
        SiteMissingError,
        Watcher,
        load_site,
        serve,
    )
    from osuforge.server.protocol import analysis_payload

    try:
        config_path = find_config(args.config)
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        site = load_site(args.site)
    except SiteMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not site.carries_token_placeholder:
        # The page would then fetch without a token and fail on every request,
        # which reads as a server fault rather than a build fault.
        print("error: the built page has no token placeholder; rebuild web/", file=sys.stderr)
        return 2

    install = config_path.parent
    replay_dir = args.replays or install / "Data" / "r"
    songs_dir = args.songs or install / "Songs"
    for label, folder in (("replay", replay_dir), ("songs", songs_dir)):
        if not folder.is_dir():
            print(f"error: no {label} folder at {folder}", file=sys.stderr)
            return 2

    index = BeatmapIndex(songs_dir)
    payloads: dict[str, Any] = {}
    skipped: dict[str, int] = {}
    corpus = CorpusState()

    # Best effort, never fatal: the journal is what knows when settings
    # changed. Without one the verdict pools everything it believes and the
    # progress view falls back to splitting on time — a weaker answer than
    # with the record, not a reason to refuse to serve.
    try:
        journal_epochs = {
            entry.replay: entry.epoch
            for entry in Journal(args.journal or default_journal_path()).entries()
        }
    except OSError:
        journal_epochs = {}

    def note(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    def prepare_replay(path: Path) -> ReplayPayload | None:
        """Build one payload, or `None` if it cannot be read yet.

        The same function serves startup and the watcher, so a play that
        appears while the page is open is prepared exactly the way the ones
        loaded at startup were. Two code paths here would drift, and the
        difference would only show as one play looking unlike the rest.
        """
        try:
            replay = parse_path(path)
        except ReplayParseError:
            # Also the ordinary case for a file osu! is still writing, so the
            # caller decides whether to retry rather than this treating it as
            # corrupt.
            return None
        beatmap_path = index.get(replay.beatmap_hash)
        if beatmap_path is None:
            return None
        try:
            plain = diffcalc.Beatmap.from_file(beatmap_path)
        except diffcalc.BeatmapError:
            return None
        # The same analysis the static page showed. It returns a reason rather
        # than None when it cannot run, and a play that cannot be analysed is
        # still worth playing back — so the failure costs the numbers, not the
        # replay.
        play = analyse(path, index)
        simulation = simulate(replay, plain)
        if not isinstance(play, str):
            # Recorded, not yet recomputed: the caller decides when to pay for
            # the statistics, so a startup scan pays once rather than per play.
            reported = replay.judgements
            corpus.add(
                path.name,
                beatmap_hash=replay.beatmap_hash,
                beatmap=f"{plain.artist} - {plain.title} [{plain.version}]",
                played_at=play.played_at,
                errors=play.errors,
                miss_rate=reported.count_miss / reported.total if reported.total else 0.0,
                accuracy=play.accuracy,
                breaks=len(play.breaks),
                agreement=play.agreement,
                agreement_reason=play.agreement_reason,
                fractional_windows=simulation.fractional_windows,
                epoch=journal_epochs.get(path.name),
            )
        return ReplayPayload.build(
            replay_name=path.name,
            beatmap=plain.with_mods(int(replay.mods)),
            simulation=simulation,
            rate=replay.rate,
            analysis=None if isinstance(play, str) else analysis_payload(play),
        )

    # Newest first and capped: every payload stays resident for the life of the
    # process, so an unbounded scan would let however much someone has played
    # decide the memory footprint.
    found = sorted(replay_dir.glob("*.osr"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in found[: args.limit]:
        payload = prepare_replay(path)
        if payload is None:
            note("could not be read")
            continue
        payloads[path.name] = payload

    print(f"site:     {site.root}", file=sys.stderr)
    print(f"beatmaps: {len(index)} indexed", file=sys.stderr)
    print(f"replays:  {len(payloads)} prepared", file=sys.stderr)
    if skipped:
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(skipped.items()))
        print(f"skipped:  {detail}", file=sys.stderr)
    if not payloads:
        print("error: nothing to serve", file=sys.stderr)
        return 2

    # Paid here, once, so the page's first request finds the answer already
    # computed — and so the terminal shows the same sentence the panel will.
    print(f"corpus:   {corpus.recompute()['summary']}", file=sys.stderr)

    broadcaster = Broadcaster()
    refreshes: set[asyncio.Task[None]] = set()

    def refresh_corpus() -> None:
        """Recompute off the loop, then tell every open page.

        A task rather than an await: the statistics take seconds, and the
        watcher that calls this is on the same loop as every WebSocket. The
        set keeps a live reference — a bare `create_task` result can be
        collected mid-flight, which cancels it.
        """

        async def push() -> None:
            try:
                fresh = await asyncio.to_thread(corpus.recompute)
                await broadcaster.send({"event": "corpus", "corpus": fresh})
            except Exception as exc:
                # Costs the panel an update, not the server its life — same
                # posture as the watcher itself.
                print(f"  corpus refresh failed: {exc}", file=sys.stderr)

        task = asyncio.get_running_loop().create_task(push())
        refreshes.add(task)
        task.add_done_callback(refreshes.discard)

    async def on_new_replay(path: Path) -> dict[str, Any] | None:
        payload = prepare_replay(path)
        if payload is None:
            return None
        payloads[path.name] = payload
        refresh_corpus()
        # The header only. It is a few kilobytes and it is everything the list
        # needs; the frames are half a megabyte and are not wanted until
        # someone selects this play.
        return {"event": "replay", "name": path.name, "header": payload.header}

    watcher = Watcher(folder=replay_dir, build=on_new_replay)
    # Everything already on disk is known, so opening the page is not a flood
    # of plays from last week.
    watcher.prime(path.name for path in found)

    port = args.port or DEFAULT_PORT
    print(f"\n  http://127.0.0.1:{port}/\n", file=sys.stderr)
    print("Leave it open while you play; new plays appear without a reload.", file=sys.stderr)
    print("Ctrl+C to stop. Nothing is left listening or written afterwards.", file=sys.stderr)
    try:
        serve(
            site=site,
            payloads=payloads,
            port=port,
            watcher=watcher,
            broadcaster=broadcaster,
            corpus=corpus.payload,
        )
    except ServerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    return 0


def _profile(args: argparse.Namespace) -> int:
    """Show or set the hardware facts that no file records."""
    if args.dpi is not None and (args.dpi_x is not None or args.dpi_y is not None):
        print("error: --dpi sets both axes; use --dpi-x and --dpi-y instead", file=sys.stderr)
        return 2
    if (args.dpi_x is None) != (args.dpi_y is None):
        # Refused rather than defaulted. Filling the missing axis from the given
        # one would silently record isotropy that was never claimed, and the
        # whole reason the axes are separate is that assuming they match is the
        # mistake.
        print("error: --dpi-x and --dpi-y must be given together", file=sys.stderr)
        return 2

    tracking_given = (
        args.asymmetric_cutoff is not None
        or args.lift_off_mm is not None
        or args.landing_mm is not None
        or args.cutoff_label is not None
    )
    if args.asymmetric_cutoff is not None and (
        args.lift_off_mm is not None or args.landing_mm is not None
    ):
        print("error: --asymmetric-cutoff already sets both distances", file=sys.stderr)
        return 2

    try:
        with open_store(args.path) as store:
            # Brings across a profile.json written before the store existed, so
            # a value already entered is not silently lost. Does nothing once
            # the store holds a pointer of its own.
            store_profile.import_legacy_profile(store)
            current = store_profile.load(store)

            if args.dpi is not None or args.dpi_x is not None or tracking_given:
                pointer = current.pointer
                if args.dpi is not None or args.dpi_x is not None:
                    dpi_x = args.dpi if args.dpi is not None else args.dpi_x
                    dpi_y = args.dpi if args.dpi is not None else args.dpi_y
                    pointer = hardware.Mouse(dpi_x=dpi_x, dpi_y=dpi_y)

                tracking = current.tracking
                if args.asymmetric_cutoff is not None:
                    tracking = hardware.Tracking.asymmetric(args.asymmetric_cutoff)
                elif tracking_given:
                    tracking = hardware.Tracking(
                        landing_mm=args.landing_mm,
                        lift_off_mm=args.lift_off_mm,
                        label=args.cutoff_label or "",
                    )

                current = hardware.Profile(pointer=pointer, tracking=tracking)
                store_profile.save(store, current, vendor=args.vendor or "", model=args.model or "")
                print(f"saved: {store.path}", file=sys.stderr)
            db_path = store.path
    except (hardware.ProfileError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    mouse = current.mouse
    tracked = current.tracking

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "path": str(db_path),
                    "mouse": (
                        None
                        if mouse is None
                        else {
                            "dpi_x": mouse.dpi_x,
                            "dpi_y": mouse.dpi_y,
                            "anisotropy": mouse.anisotropy,
                        }
                    ),
                    "tracking": (
                        None
                        if tracked is None
                        else {
                            "landing_mm": tracked.landing_mm,
                            "lift_off_mm": tracked.lift_off_mm,
                            "label": tracked.label,
                            "of_concern": tracked.of_concern,
                        }
                    ),
                },
                indent=2,
            )
        )
        return 0

    if mouse is None:
        print("no mouse recorded.", file=sys.stderr)
        print("  forge profile --dpi 800", file=sys.stderr)
        print(
            "\nOne number is all most mice have, and all this needs. The rest is for "
            "mice that expose more, and leaving it unset costs precision in the advice "
            "rather than making it wrong.",
            file=sys.stderr,
        )
        print(
            "  forge profile --dpi-x 1400 --dpi-y 1350   # separate DPI per axis", file=sys.stderr
        )
        print(
            "  forge profile --asymmetric-cutoff 1       # sensor cut-off preset", file=sys.stderr
        )
    else:
        print(f"mouse: {mouse.describe()}", file=sys.stderr)
        x, y = mouse.step_fraction()
        if mouse.axes_differ:
            print(
                f"  a 50-count DPI step is {x:.2%} horizontally, {y:.2%} vertically",
                file=sys.stderr,
            )
        else:
            print(f"  a 50-count DPI step is {x:.2%}", file=sys.stderr)

    if tracked is not None:
        print(f"tracking: {tracked.describe()}", file=sys.stderr)
        if tracked.of_concern:
            print(
                "  above the height where lift-off is worth mentioning: some cursor "
                "motion while repositioning is recorded as though it were aim",
                file=sys.stderr,
            )

    print(
        "\ndeclared, not measured — no file on disk records any of this, so it is "
        "taken on trust and any finding that uses it says so",
        file=sys.stderr,
    )
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

    diagnose = subparsers.add_parser(
        "diagnose",
        parents=[common],
        help="what keeps happening across every replay, not what one play did",
        description=(
            "Reads every replay in the folder, simulates each against its beatmap, and "
            "reports what the corpus supports: whether your timing is biased, whether it "
            "is merely inconsistent, and whether a weakness belongs to one map or "
            "follows you everywhere. Refuses to answer below ten replays or three "
            "sessions, and says which of the two is missing. Only replays played under "
            "the settings now in force are pooled, because an offset measurement is "
            "about the configuration it was measured under."
        ),
    )
    diagnose.add_argument("--replays", type=Path, default=None, help="folder of .osr files")
    diagnose.add_argument("--songs", type=Path, default=None, help="beatmap folder")
    diagnose.add_argument(
        "--journal",
        type=Path,
        default=None,
        help=f"the settings history to group by (default: {default_journal_path()})",
    )
    diagnose.add_argument(
        "--db",
        type=Path,
        default=None,
        help="path to osu!.db, read for per-beatmap local offsets",
    )
    diagnose.add_argument(
        "--all-epochs",
        action="store_true",
        help="pool replays across settings changes; averages configurations together",
    )
    diagnose.set_defaults(func=_diagnose)

    live = subparsers.add_parser(
        "live",
        parents=[common],
        help="write a page that updates as you finish plays",
        description=(
            "Writes a self-contained HTML file and rewrites it every time a play "
            "finishes, so a browser left open on a second monitor shows what the "
            "last play did within a second or two of it ending. Nothing listens on "
            "a port and nothing is left running when you stop it. A play that leaves "
            "no replay — a quick retry, or a fail — cannot appear here."
        ),
    )
    live.add_argument("--replays", type=Path, default=None, help="folder of .osr files")
    live.add_argument("--songs", type=Path, default=None, help="beatmap folder")
    live.add_argument(
        "--history",
        type=int,
        default=10,
        metavar="N",
        help="plays already on disk to show at startup (0 for none, default: 10)",
    )
    live.add_argument(
        "--page",
        type=Path,
        default=None,
        help=f"where to write the page (default: {default_page_path()})",
    )
    live.set_defaults(func=_live)

    serve_cmd = subparsers.add_parser(
        "serve",
        parents=[common],
        help="serve the page that plays your replays back",
        description=(
            "Binds 127.0.0.1 and nothing else. A per-run bearer token is published "
            "beside the tool's own data, the page carries it, and both are gone when "
            "the command stops.\n\n"
            "A busy port is a failure rather than a reason to move: a client with the "
            "old port cached would reach whatever else is there, and the neighbouring "
            "ports belong to other tools of this kind.\n\n"
            "The page is built from web/ and is not committed. Build it once with "
            "`cd web && npm install && npm run build`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    serve_cmd.add_argument("--replays", type=Path, default=None, help="folder of .osr files")
    serve_cmd.add_argument("--songs", type=Path, default=None, help="beatmap folder")
    serve_cmd.add_argument(
        "--journal",
        type=Path,
        default=None,
        help=(
            f"the settings history the corpus panel splits on (default: {default_journal_path()})"
        ),
    )
    serve_cmd.add_argument(
        "--site", type=Path, default=None, help="built web/dist (default: found from the checkout)"
    )
    serve_cmd.add_argument(
        "--port", type=int, default=None, help=f"port to bind (default: {DEFAULT_PORT})"
    )
    serve_cmd.add_argument(
        "--limit",
        type=int,
        default=25,
        metavar="N",
        help="most recent replays to prepare (default: 25); each stays in memory",
    )
    serve_cmd.set_defaults(func=_serve)

    profile = subparsers.add_parser(
        "profile",
        help="record hardware that no file on disk knows about",
        description=(
            "A replay stores the cursor position in osu! pixels and never the mouse "
            "counts behind it, so nothing on disk records your DPI. Without it, a "
            "pointer finding can only say 'your gain is 2% high'; with it, that "
            "becomes a number of DPI counts and an answer to whether a 50-count step "
            "is too coarse to be worth taking.\n\n"
            "Stored outside the osu! install, like everything else this tool writes. "
            "Run with no arguments to see what is recorded."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    profile.add_argument(
        "--dpi",
        type=int,
        metavar="N",
        help="mouse DPI, when both axes are the same",
    )
    profile.add_argument(
        "--dpi-x",
        type=int,
        metavar="N",
        help="horizontal DPI, when the axes differ",
    )
    profile.add_argument(
        "--dpi-y",
        type=int,
        metavar="N",
        help="vertical DPI, when the axes differ",
    )
    profile.add_argument(
        "--asymmetric-cutoff",
        type=int,
        choices=sorted(hardware.ASYMMETRIC_CUTOFF_PRESETS),
        metavar="N",
        help=(
            "sensor cut-off preset: "
            + "; ".join(
                f"{n} = landing {land:g}mm, lift-off {lift:g}mm"
                for n, (land, lift) in sorted(hardware.ASYMMETRIC_CUTOFF_PRESETS.items())
            )
        ),
    )
    profile.add_argument(
        "--lift-off-mm",
        type=float,
        metavar="MM",
        help="height at which the sensor stops tracking, if you know it in mm",
    )
    profile.add_argument(
        "--landing-mm",
        type=float,
        metavar="MM",
        help="height at which the sensor resumes tracking on the way down",
    )
    profile.add_argument(
        "--cutoff-label",
        metavar="TEXT",
        help=(
            "vendor setting when the millimetres are not published — Low, Medium, "
            "High. Recorded as a label with the distances left unknown, rather than "
            "as invented numbers"
        ),
    )
    profile.add_argument("--vendor", metavar="NAME", help="mouse maker, free text")
    profile.add_argument("--model", metavar="NAME", help="mouse model, free text")
    profile.add_argument(
        "--path",
        type=Path,
        default=None,
        help=f"where the store lives (default: {default_db_path()})",
    )
    profile.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    profile.set_defaults(func=_profile)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
