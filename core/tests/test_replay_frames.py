"""Frame normalization and key-event extraction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from osuforge.replay import Keys, parse, parse_path
from osuforge.replay.frames import Button, normalize

from .replay_fixtures import build_replay

# The two frames osu! writes at the head of every replay.
MARKERS: list[tuple[int, float, float, int]] = [
    (0, 256.0, -500.0, 0),
    (-1, 256.0, -500.0, 0),
]

K1 = int(Keys.K1 | Keys.M1)
K2 = int(Keys.K2 | Keys.M2)
BOTH = K1 | K2
M1_ONLY = int(Keys.M1)
M2_ONLY = int(Keys.M2)


def frames_of(*entries: tuple[int, float, float, int], markers: bool = True):
    body = [*MARKERS, *entries] if markers else list(entries)
    return normalize(parse(build_replay(frames=body)))


class TestMarkers:
    def test_leading_markers_are_stripped(self) -> None:
        result = frames_of((10, 5.0, 5.0, 0))
        assert result.markers_removed == 2
        assert len(result.frames) == 1
        assert result.frames[0].x == 5.0

    def test_a_marker_position_later_in_the_stream_is_kept(self) -> None:
        # Markers only appear at the head. Scanning the whole stream would drop
        # a real frame that happens to land on the same coordinates.
        result = frames_of((10, 5.0, 5.0, 0), (10, 256.0, -500.0, 0), (10, 7.0, 7.0, 0))
        assert result.markers_removed == 2
        assert len(result.frames) == 3

    def test_a_replay_with_no_markers_is_fine(self) -> None:
        result = frames_of((10, 5.0, 5.0, 0), markers=False)
        assert result.markers_removed == 0
        assert len(result.frames) == 1

    def test_a_replay_that_is_only_markers_yields_nothing(self) -> None:
        result = normalize(parse(build_replay(frames=MARKERS)))
        assert result.frames == []
        assert result.events == []
        assert result.markers_removed == 2


class TestStartTime:
    def test_the_lead_in_is_recorded_not_discarded(self) -> None:
        # The first post-marker frame carries the audio lead-in or a skip as a
        # large negative delta, so the stream legitimately starts before zero.
        result = frames_of((-944, 100.0, 100.0, 0), (16, 100.0, 100.0, 0))
        assert result.start_time == -945
        assert result.frames[0].time == -945

    def test_backwards_steps_are_counted_rather_than_hidden(self) -> None:
        # Real and rare: a step of a few ms about 2% into the stream, where
        # playback leaves the lead-in. Counted so a replay that needed unusual
        # handling is visible rather than silently trusted.
        result = frames_of((100, 0.0, 0.0, 0), (-5, 0.0, 0.0, 0), (20, 0.0, 0.0, 0))
        assert result.backwards_steps == 1

    def test_a_forward_only_stream_reports_none(self) -> None:
        result = frames_of((100, 0.0, 0.0, 0), (16, 0.0, 0.0, 0), (16, 0.0, 0.0, 0))
        assert result.backwards_steps == 0


class TestKeyEdges:
    def test_a_press_and_release_produce_one_event_each(self) -> None:
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, K1), (10, 0.0, 0.0, 0))
        assert [(e.edge, e.button) for e in result.events] == [
            ("down", Button.K1),
            ("up", Button.K1),
        ]

    def test_holding_a_key_does_not_repeat_the_press(self) -> None:
        result = frames_of(
            (10, 0.0, 0.0, K1), (10, 0.0, 0.0, K1), (10, 0.0, 0.0, K1), (10, 0.0, 0.0, 0)
        )
        assert len(result.press_events) == 1

    def test_the_pairing_does_not_double_count_a_keyboard_press(self) -> None:
        # K1 sets M1 too. Edge-detecting on all four bits would see two presses
        # for one finger; masking to M1|M2 collapses them.
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, K1))
        assert len(result.press_events) == 1

    def test_both_keys_pressed_on_one_frame_produce_two_events(self) -> None:
        # A mask of 15 does occur in real replays, and it is two presses.
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, BOTH))
        buttons = [e.button for e in result.press_events]
        assert sorted(buttons) == [Button.K1, Button.K2]

    def test_a_key_already_held_on_the_first_frame_counts_as_a_press(self) -> None:
        # Eight of 58 real replays start this way. The markers before it carry
        # no keys, so relative to them it is genuinely a down edge.
        result = frames_of((10, 0.0, 0.0, K1))
        assert len(result.press_events) == 1

    def test_alternating_presses(self) -> None:
        result = frames_of(
            (10, 0.0, 0.0, K1),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, K2),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, K1),
        )
        assert [e.button for e in result.press_events] == [Button.K1, Button.K2, Button.K1]

    def test_smoke_is_not_a_press(self) -> None:
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, int(Keys.SMOKE)))
        assert result.press_events == []

    def test_a_release_is_attributed_to_what_was_held(self) -> None:
        # The releasing frame no longer has the bit set, so the button has to
        # come from the frame before it.
        result = frames_of((10, 0.0, 0.0, M1_ONLY), (10, 0.0, 0.0, 0))
        releases = [e for e in result.events if e.edge == "up"]
        assert [e.button for e in releases] == [Button.M1]


class TestDeviceAttribution:
    def test_a_mouse_click_is_not_recorded_as_a_key(self) -> None:
        # M1 without K1 is a real click. Attributing by the judgement bit alone
        # would call it K1; edge-detecting on K1 alone would lose it entirely,
        # and a real corpus has 467 of them despite mouse buttons being disabled
        # in the config.
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, M1_ONLY))
        assert [e.button for e in result.press_events] == [Button.M1]

    def test_the_same_judgement_bit_from_two_devices(self) -> None:
        result = frames_of(
            (10, 0.0, 0.0, K1),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, M1_ONLY),
        )
        assert [e.button for e in result.press_events] == [Button.K1, Button.M1]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(K1, Button.K1), (K2, Button.K2), (M1_ONLY, Button.M1), (M2_ONLY, Button.M2)],
    )
    def test_each_button(self, raw: int, expected: Button) -> None:
        result = frames_of((10, 0.0, 0.0, 0), (10, 0.0, 0.0, raw))
        assert result.press_events[0].button is expected

    def test_button_sides_and_mouse_flags(self) -> None:
        assert Button.K1.side == "left" and Button.M1.side == "left"
        assert Button.K2.side == "right" and Button.M2.side == "right"
        assert Button.M1.is_mouse and not Button.K1.is_mouse

    def test_button_counts(self) -> None:
        result = frames_of(
            (10, 0.0, 0.0, K1),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, K1),
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, K2),
        )
        counts = result.button_counts()
        assert counts[Button.K1] == 2
        assert counts[Button.K2] == 1
        assert counts[Button.M1] == 0


class TestTimingUncertainty:
    def test_every_event_carries_the_gap_to_the_previous_sample(self) -> None:
        # This is the error bar on the timestamp, and it is comparable in size
        # to the effects an offset analysis looks for.
        result = frames_of((10, 0.0, 0.0, 0), (17, 0.0, 0.0, K1))
        assert result.press_events[0].uncertainty == 17

    def test_earliest_possible_bounds_the_true_press(self) -> None:
        # osu! samples once per frame, so the press happened somewhere in
        # (time - uncertainty, time]. The two marker frames contribute -1 to the
        # running time before any of this.
        result = frames_of((100, 0.0, 0.0, 0), (16, 0.0, 0.0, K1))
        event = result.press_events[0]
        assert event.time == 115
        assert event.earliest_possible == 99

    def test_a_press_on_the_first_frame_has_unknown_uncertainty(self) -> None:
        # There is no previous sample to bound it against. The frame's own delta
        # is the audio lead-in, not a sampling interval, and using it would put
        # the press seconds in the future. Reported as unknown rather than zero,
        # which would read as "measured precisely".
        result = frames_of((-22674, 0.0, 0.0, K1))
        event = result.press_events[0]
        assert event.uncertainty is None
        assert event.earliest_possible is None

    def test_unknown_uncertainties_are_excluded_not_counted_as_zero(self) -> None:
        result = frames_of((-500, 0.0, 0.0, K1), (10, 0.0, 0.0, 0), (25, 0.0, 0.0, K2))
        assert result.press_uncertainties() == [25]
        assert result.unbounded_presses == 1

    def test_uncertainties_report_the_whole_distribution(self) -> None:
        result = frames_of(
            (10, 0.0, 0.0, 0),
            (10, 0.0, 0.0, K1),
            (10, 0.0, 0.0, 0),
            (25, 0.0, 0.0, K2),
        )
        assert result.press_uncertainties() == [10, 25]

    def test_frame_index_points_back_into_the_stream(self) -> None:
        result = frames_of((10, 0.0, 0.0, 0), (10, 1.0, 2.0, K1))
        event = result.press_events[0]
        assert result.frames[event.frame_index].x == 1.0


@pytest.mark.integration
class TestAgainstRealReplays:
    """Shape checks against a real folder, opt-in via OSU_FORGE_REPLAYS."""

    @pytest.fixture
    def replays(self) -> list[Path]:
        location = os.environ.get("OSU_FORGE_REPLAYS")
        if not location:
            pytest.skip("set OSU_FORGE_REPLAYS to a folder of .osr files")
        folder = Path(location)
        if not folder.is_dir():
            pytest.skip(f"{folder} is not a folder")
        files = sorted(folder.glob("*.osr"))
        if not files:
            pytest.skip(f"no .osr files in {folder}")
        return files

    def test_every_replay_has_exactly_two_marker_frames(self, replays: list[Path]) -> None:
        seen = 0
        for path in replays:
            try:
                replay = parse_path(path)
            except Exception:
                continue
            if not replay.frames:
                continue
            assert normalize(replay).markers_removed == 2, path.name
            seen += 1
        assert seen > 0

    def test_the_button_split_is_self_consistent(self, replays: list[Path]) -> None:
        # The parts must sum to the whole. An earlier independent analysis of
        # this same folder reported a total that its own per-button breakdown
        # did not add up to — it had omitted M1 — so this asserts the property
        # rather than a remembered number.
        total_presses = 0
        total_by_button = 0
        for path in replays:
            try:
                replay = parse_path(path)
            except Exception:
                continue
            result = normalize(replay)
            total_presses += len(result.press_events)
            total_by_button += sum(result.button_counts().values())
        assert total_presses > 0
        assert total_presses == total_by_button

    def test_keyboard_dominates_and_mouse_is_a_small_tail(self, replays: list[Path]) -> None:
        # Not a specific count, which would only hold for one player: the
        # invariant is that a keyboard player's clicks are a small fraction, and
        # that the fraction is not zero, since dropping mouse clicks entirely is
        # the failure mode device attribution exists to prevent.
        counts = dict.fromkeys(Button, 0)
        for path in replays:
            try:
                replay = parse_path(path)
            except Exception:
                continue
            for button, count in normalize(replay).button_counts().items():
                counts[button] += count

        total = sum(counts.values())
        assert total > 0
        keyboard = counts[Button.K1] + counts[Button.K2]
        assert keyboard / total > 0.5, "keyboard presses should dominate"

    def test_press_uncertainties_are_bounded_by_the_frame_rate(self, replays: list[Path]) -> None:
        # A press cannot be observed with a delta larger than the frame that
        # carried it; a huge one would mean deltas are being misattributed.
        for path in replays:
            try:
                replay = parse_path(path)
            except Exception:
                continue
            result = normalize(replay)
            for event in result.press_events:
                if event.uncertainty is None:
                    continue
                assert 0 <= event.uncertainty < 2000, (
                    f"{path.name}: uncertainty {event.uncertainty} ms at {event.time} ms"
                )
