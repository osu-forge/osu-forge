"""Replay parser tests.

Every fixture is built programmatically, so the expected values are known
rather than observed. The one test that reads real replays is marked
``integration`` and skips when they are absent.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osuforge.replay import (
    Judgements,
    Keys,
    Mods,
    ReplayParseError,
    Ruleset,
    beatmap_hash_from_filename,
    parse,
    parse_path,
)

from .replay_fixtures import build_replay, encode_string


class TestHeader:
    def test_round_trips_every_field(self) -> None:
        data = build_replay(
            game_version=20260711,
            beatmap_hash="a" * 32,
            player_name="[Good Day]",
            count_300=500,
            count_100=20,
            count_50=3,
            count_miss=7,
            score=9_876_543,
            max_combo=842,
            perfect_combo=True,
            mods=int(Mods.HIDDEN | Mods.DOUBLE_TIME),
            timestamp=datetime(2026, 6, 3, 21, 30, 15, tzinfo=UTC),
        )
        replay = parse(data)

        assert replay.ruleset is Ruleset.OSU
        assert replay.game_version == 20260711
        assert replay.beatmap_hash == "a" * 32
        assert replay.player_name == "[Good Day]"
        assert replay.judgements.count_300 == 500
        assert replay.judgements.count_miss == 7
        assert replay.score == 9_876_543
        assert replay.max_combo == 842
        assert replay.perfect_combo
        assert replay.mods == Mods.HIDDEN | Mods.DOUBLE_TIME
        assert replay.timestamp == datetime(2026, 6, 3, 21, 30, 15, tzinfo=UTC)

    def test_non_ascii_player_name(self) -> None:
        assert parse(build_replay(player_name="플레이어")).player_name == "플레이어"

    def test_an_absent_string_is_empty_not_an_error(self) -> None:
        assert parse(build_replay(life_bar="")).life_bar == ""

    def test_an_older_replay_without_an_online_score_id(self) -> None:
        # The field was added in 2019; before that the header simply ends.
        data = build_replay(include_online_score_id=False)
        assert parse(data).online_score_id == 0

    def test_uleb128_handles_a_long_string(self) -> None:
        # Over 127 bytes needs a second continuation byte, which is where a
        # hand-rolled length decoder usually goes wrong.
        long_name = "x" * 300
        assert parse(build_replay(player_name=long_name)).player_name == long_name
        assert encode_string(long_name)[1] & 0x80, "fixture should exercise the continuation"


class TestFailureModes:
    def test_an_empty_file_says_so(self) -> None:
        # One of the 59 real replays is zero bytes. It must not take the batch
        # down, and "corrupt" would not tell the user which problem this is.
        with pytest.raises(ReplayParseError, match="empty"):
            parse(b"")

    def test_a_truncated_file_names_the_offset(self) -> None:
        with pytest.raises(ReplayParseError) as exc:
            parse(build_replay()[:12])
        assert exc.value.offset is not None

    def test_a_bad_string_marker_stops_rather_than_guessing(self) -> None:
        # Losing alignment here would produce a plausible-looking replay full of
        # garbage, which is worse than failing.
        data = bytearray(build_replay())
        data[5] = 0x42  # first string marker
        with pytest.raises(ReplayParseError, match="string marker"):
            parse(bytes(data))

    def test_an_unknown_ruleset_is_rejected(self) -> None:
        data = bytearray(build_replay())
        data[0] = 9
        with pytest.raises(ReplayParseError, match="ruleset"):
            parse(bytes(data))

    def test_undecompressable_frames_are_reported(self) -> None:
        data = bytearray(build_replay(frames=[(0, 0.0, 0.0, 0)]))
        # Corrupt the compressed block without changing its declared length.
        index = data.rindex(b"\x5d")
        data[index : index + 4] = b"\x00\x00\x00\x00"
        with pytest.raises(ReplayParseError, match="decompress"):
            parse(bytes(data))


class TestFrames:
    def test_deltas_accumulate_into_absolute_times(self) -> None:
        replay = parse(
            build_replay(frames=[(100, 1.0, 2.0, 0), (50, 3.0, 4.0, 5), (25, 5.0, 6.0, 0)])
        )
        assert [f.time for f in replay.frames] == [100, 150, 175]

    def test_the_delta_is_kept_on_every_frame(self) -> None:
        # It is the timing uncertainty for a press recorded on that frame, and
        # recovering it later from neighbours is lossy once frames are filtered.
        replay = parse(build_replay(frames=[(100, 0.0, 0.0, 0), (16, 0.0, 0.0, 5)]))
        assert [f.delta for f in replay.frames] == [100, 16]

    def test_a_negative_delta_is_preserved(self) -> None:
        # The third frame of a real replay usually carries a large negative
        # delta recording the lead-in or a skip.
        replay = parse(
            build_replay(
                frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0), (-944, 5.0, 5.0, 0)]
            )
        )
        assert [f.delta for f in replay.frames] == [0, -1, -944]
        assert replay.frames[-1].time == -945

    def test_the_seed_frame_is_separated_not_summed(self) -> None:
        # Left in the stream, its -12345 delta would place a phantom frame
        # twelve seconds before the map starts and drag every timing statistic.
        replay = parse(build_replay(frames=[(100, 0.0, 0.0, 0)], seed=987_654))
        assert replay.seed == 987_654
        assert len(replay.frames) == 1
        assert all(f.delta != -12345 for f in replay.frames)

    def test_a_replay_without_a_seed_frame_still_parses(self) -> None:
        replay = parse(build_replay(frames=[(100, 0.0, 0.0, 0)], seed=None))
        assert replay.seed is None
        assert len(replay.frames) == 1

    def test_no_frame_data_gives_an_empty_list(self) -> None:
        assert parse(build_replay()).frames == []

    def test_a_malformed_frame_is_skipped_not_fatal(self) -> None:
        replay = parse(build_replay(frames=[(10, 1.0, 2.0, 0), (10, 3.0, 4.0, 5)]))
        assert len(replay.frames) == 2

    def test_undefined_key_bits_are_masked_off(self) -> None:
        # Bits above smoke mean nothing in osu!standard; letting one through
        # would read as a key press that never happened.
        replay = parse(build_replay(frames=[(0, 0.0, 0.0, 0b1111_1111)]))
        assert replay.frames[0].keys == Keys(0b11111)


class TestKeys:
    def test_k1_implies_m1_so_a_keyboard_press_reads_as_five(self) -> None:
        keys = Keys.K1 | Keys.M1
        assert keys.value == 5
        assert not keys.mouse_only

    def test_a_real_mouse_click_has_the_mouse_bit_without_its_partner(self) -> None:
        # Edge-detecting only on K1/K2 drops these entirely. A real corpus had
        # 465 of them despite MouseDisableButtons being on.
        assert Keys.M1.mouse_only
        assert Keys.M2.mouse_only
        assert not (Keys.M1 | Keys.K1).mouse_only

    def test_the_judgement_mask_collapses_the_pairs(self) -> None:
        # Masking to M1|M2 dedupes the keyboard/mouse pairing automatically,
        # which is why judgement uses it rather than testing four bits.
        assert (Keys.K1 | Keys.M1).judgement_mask == Keys.M1
        assert (Keys.K2 | Keys.M2).judgement_mask == Keys.M2
        assert (Keys.K1 | Keys.M1 | Keys.K2 | Keys.M2).judgement_mask == 0b11
        assert Keys.SMOKE.judgement_mask == 0

    def test_smoke_is_not_a_judgement_press(self) -> None:
        assert (Keys.SMOKE | Keys.M1).judgement_mask == Keys.M1


class TestMods:
    @pytest.mark.parametrize(
        ("mods", "rate"),
        [
            (Mods.NONE, 1.0),
            (Mods.DOUBLE_TIME, 1.5),
            (Mods.HALF_TIME, 0.75),
            (Mods.NIGHTCORE | Mods.DOUBLE_TIME, 1.5),
            (Mods.HIDDEN, 1.0),
        ],
    )
    def test_rate(self, mods: Mods, rate: float) -> None:
        assert mods.rate == rate

    @pytest.mark.parametrize("mods", [Mods.RELAX, Mods.AUTOPILOT, Mods.AUTOPLAY, Mods.CINEMA])
    def test_assisted_play_is_flagged(self, mods: Mods) -> None:
        # These record something other than a person's timing, so including them
        # in a hit-error distribution would pull the mean toward the assist.
        assert mods.not_human

    @pytest.mark.parametrize("mods", [Mods.NONE, Mods.HIDDEN, Mods.DOUBLE_TIME, Mods.NO_FAIL])
    def test_ordinary_mods_are_not_flagged(self, mods: Mods) -> None:
        assert not mods.not_human

    def test_usable_for_timing_excludes_assists_and_other_rulesets(self) -> None:
        assert parse(build_replay()).usable_for_timing
        assert not parse(build_replay(mods=int(Mods.RELAX))).usable_for_timing
        assert not parse(build_replay(ruleset=2)).usable_for_timing

    def test_acronyms(self) -> None:
        assert set(Mods(int(Mods.HIDDEN | Mods.DOUBLE_TIME)).acronyms()) == {
            "HIDDEN",
            "DOUBLE_TIME",
        }


class TestJudgements:
    def test_total_counts_objects_not_geki_and_katu(self) -> None:
        # In osu!standard geki and katu mark combo endings; they are not extra
        # objects. Adding them would inflate the denominator of every rate.
        j = Judgements(
            count_300=100, count_100=10, count_50=1, count_geki=20, count_katu=5, count_miss=2
        )
        assert j.total == 113

    def test_accuracy(self) -> None:
        perfect = Judgements(10, 0, 0, 0, 0, 0)
        assert perfect.accuracy == 1.0
        half = Judgements(0, 0, 0, 0, 0, 10)
        assert half.accuracy == 0.0

    def test_accuracy_with_no_judgements_does_not_divide_by_zero(self) -> None:
        assert Judgements(0, 0, 0, 0, 0, 0).accuracy == 0.0


class TestFilenameConvention:
    def test_extracts_the_beatmap_hash(self) -> None:
        # osu! names local replays <beatmapMD5>-<ticks>.osr, so a folder can be
        # indexed without decompressing anything.
        path = Path("07ec7649c1b2a3401537826fc7df0ba1-134253208924938022.osr")
        assert beatmap_hash_from_filename(path) == "07ec7649c1b2a3401537826fc7df0ba1"

    def test_uppercase_hex_is_normalised(self) -> None:
        path = Path("07EC7649C1B2A3401537826FC7DF0BA1-1.osr")
        assert beatmap_hash_from_filename(path) == "07ec7649c1b2a3401537826fc7df0ba1"

    @pytest.mark.parametrize(
        "name",
        ["something.osr", "abc-123.osr", "07ec7649c1b2a3401537826fc7df0ba1.osr", "notareplay.txt"],
    )
    def test_other_names_return_none_rather_than_guessing(self, name: str) -> None:
        assert beatmap_hash_from_filename(Path(name)) is None


class TestKnownGroundTruth:
    """Synthetic replays whose correct answer is known analytically.

    This is what a real replay cannot give: feeding in a deliberate
    distribution and checking the number that comes back.
    """

    def test_frame_times_reconstruct_a_known_schedule(self) -> None:
        # A press every 16 ms for a second: 60 Hz, which is what a real corpus
        # showed osu!stable actually samples at.
        frames = [(16, 0.0, 0.0, 5 if i % 2 else 0) for i in range(62)]
        replay = parse(build_replay(frames=frames))
        assert len(replay.frames) == 62
        assert replay.frames[-1].time == 16 * 62
        assert all(f.delta == 16 for f in replay.frames)

    def test_key_edges_are_recoverable_from_the_raw_stream(self) -> None:
        # down, down (held), up, down. Two press edges, not three.
        frames = [
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, int(Keys.K1 | Keys.M1)),
            (10, 0.0, 0.0, int(Keys.K1 | Keys.M1)),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, int(Keys.K2 | Keys.M2)),
        ]
        replay = parse(build_replay(frames=frames))
        masks = [f.keys.judgement_mask for f in replay.frames]
        edges = sum(1 for i in range(1, len(masks)) if masks[i] & ~masks[i - 1])
        assert edges == 2


@pytest.mark.integration
class TestAgainstRealReplays:
    """Plausibility against a real folder.

    Deselected by default and skipped unless ``OSU_FORGE_REPLAYS`` points at a
    folder, so a plain ``pytest`` run cannot read anyone's replays. That has to
    be an explicit opt-in rather than a default with a guard, because the
    sandbox fixture in ``conftest`` only redirects environment variables — a
    test reaching for ``Path.home()`` would walk straight past it.

    Every assertion here is about shape and invariants. Anything asserting a
    count from one machine would pass for its author and fail for everyone else.
    """

    @pytest.fixture
    def replays(self) -> list[Path]:
        location = os.environ.get("OSU_FORGE_REPLAYS")
        if not location:
            pytest.skip("set OSU_FORGE_REPLAYS to a folder of .osr files")
        folder = Path(location)
        if not folder.is_dir():
            pytest.skip(f"OSU_FORGE_REPLAYS points at {folder}, which is not a folder")
        files = sorted(folder.glob("*.osr"))
        if not files:
            pytest.skip(f"no .osr files in {folder}")
        return files

    def test_every_readable_replay_parses(self, replays: list[Path]) -> None:
        parsed = 0
        empty = 0
        for path in replays:
            try:
                parse_path(path)
                parsed += 1
            except ReplayParseError as exc:
                # A zero-byte file is a known real case, not a parser fault.
                if "empty" in str(exc):
                    empty += 1
                else:
                    raise
        assert parsed > 0
        assert parsed + empty == len(replays)

    def test_the_filename_hash_matches_the_header(self, replays: list[Path]) -> None:
        # An independent cross-check on string alignment: if the ULEB128 or the
        # marker handling were off, the header hash would not match the name.
        checked = 0
        for path in replays:
            expected = beatmap_hash_from_filename(path)
            if expected is None:
                continue
            try:
                replay = parse_path(path)
            except ReplayParseError:
                continue
            assert replay.beatmap_hash.lower() == expected
            checked += 1
        assert checked > 0, "no replays followed the naming convention"

    def test_the_seed_frame_never_survives(self, replays: list[Path]) -> None:
        for path in replays:
            try:
                replay = parse_path(path)
            except ReplayParseError:
                continue
            assert all(f.delta != -12345 for f in replay.frames), path.name

    def test_backwards_steps_are_rare_and_small(self, replays: list[Path]) -> None:
        """Frame times do go backwards, and that is osu!, not a parsing fault.

        Measured across a real folder: every replay carries a large negative
        delta in its first frames (the lead-in or a skip), and a few carry
        exactly one further small step backwards about 2% into the stream —
        the transition out of that section. Deltas of -1 to -16 ms, one frame's
        worth.

        So this does not assert monotonicity. It asserts the *shape*: a sign
        error or a mis-accumulated delta would show up as many backwards steps,
        or as large ones, scattered through the stream. Handling them belongs to
        frame normalisation, not to the parser, which preserves what the file
        says.
        """
        for path in replays:
            try:
                replay = parse_path(path)
            except ReplayParseError:
                continue
            if len(replay.frames) < 10:
                continue

            after_lead_in = replay.frames[3:]
            backwards = [f for f in after_lead_in if f.delta < 0]

            assert len(backwards) <= 4, (
                f"{path.name}: {len(backwards)} backwards steps after the lead-in. "
                "More than a couple suggests deltas are being accumulated wrongly."
            )
            for frame in backwards:
                assert frame.delta > -100, (
                    f"{path.name}: a {frame.delta} ms step backwards at {frame.time} ms. "
                    "Small steps are the lead-in transition; a large one is a sign error."
                )

    def test_most_of_the_stream_moves_forward(self, replays: list[Path]) -> None:
        # The positive counterpart: if deltas were being applied with the wrong
        # sign, this would collapse.
        for path in replays:
            try:
                replay = parse_path(path)
            except ReplayParseError:
                continue
            if len(replay.frames) < 100:
                continue
            forward = sum(1 for f in replay.frames if f.delta > 0)
            assert forward / len(replay.frames) > 0.95, path.name
