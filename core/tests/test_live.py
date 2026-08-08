"""The page that updates as plays finish."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.analysis.patterns import Attribution
from osuforge.live.render import REFRESH_SECONDS, render
from osuforge.live.watch import BeatmapIndex, Play, Session, analyse
from osuforge.replay.frames import Button
from osuforge.replay.simulate import Grade
from osuforge.replay.validate import Agreement

MINIMAL_MAP = (
    "osu file format v14\n"
    "[General]\nMode: 0\n"
    "[Metadata]\nArtist:A\nTitle:T\nVersion:V\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
    "[HitObjects]\n100,100,1000,1,0\n"
)


def play(
    *,
    title: str = "Some Song",
    agreement: Agreement = Agreement.EXACT,
    errors: list[float] | None = None,
) -> Play:
    return Play(
        replay_name="a.osr",
        played_at=datetime(2026, 8, 9, 21, 30, tzinfo=UTC),
        artist="Artist",
        title=title,
        version="Insane",
        accuracy=0.9812,
        counts={Grade.THREE_HUNDRED: 500, Grade.HUNDRED: 20, Grade.FIFTY: 1, Grade.MISS: 3},
        agreement=agreement,
        agreement_reason="all judgements reproduce",
        errors=errors if errors is not None else [float(i % 21) - 10.0 for i in range(400)],
        aim_errors=[0.1 * (i % 9) for i in range(400)],
        presses={Button.K1: 260, Button.K2: 240},
        attribution=Attribution(accuracy=0.98, total_loss=10.0, judged=524, shares=[]),
    )


class TestPage:
    def test_an_empty_session_says_what_to_do(self) -> None:
        page = render(Session())
        assert "Finish a play" in page
        # The limitation belongs on the page, not only in a docstring: a player
        # who retries after a mistake will otherwise wonder why nothing appears.
        assert "Quick-retrying" in page

    def test_a_play_appears_with_its_numbers(self) -> None:
        session = Session()
        session.add(play())
        page = render(session)
        assert "Some Song" in page
        assert "98.12%" in page
        assert "unstable rate" in page

    def test_nothing_is_fetched_from_anywhere(self) -> None:
        # The report has to work with no network, and a page that fetches
        # nothing cannot say what it is about by fetching it. This is the same
        # property CI asserts over generated reports.
        session = Session()
        session.add(play())
        page = render(session)
        assert "http://" not in page
        assert "https://" not in page
        assert not re.search(r"\bsrc\s*=", page)
        assert "<script" not in page.lower()

    def test_a_beatmap_title_cannot_inject_markup(self) -> None:
        # Titles come from files on disk, which are not necessarily the user's.
        session = Session()
        session.add(play(title="<script>alert(1)</script>"))
        page = render(session)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_a_play_that_failed_the_screen_is_shown_as_such(self) -> None:
        # Dropping it silently would leave a play the user knows they finished
        # missing from the page with no explanation.
        session = Session()
        session.add(play(agreement=Agreement.MISMATCH))
        page = render(session)
        assert "Not analysed" in page
        assert "unstable rate" not in page.split("Not analysed")[1]

    def test_the_chart_needs_enough_hits_to_be_worth_drawing(self) -> None:
        few = Session()
        few.add(play(errors=[1.0, 2.0]))
        assert "<svg" not in render(few)

        many = Session()
        many.add(play())
        assert "<svg" in render(many)

    def test_a_single_session_is_not_given_a_confidence_interval(self) -> None:
        # Hits inside one sitting are not independent, so an interval from one
        # would be far narrower than the truth.
        session = Session()
        session.add(play())
        page = render(session)
        assert "No confidence interval" in page
        assert "several separate sessions" in page

    def test_the_page_reloads_itself(self) -> None:
        assert f'content="{REFRESH_SECONDS}"' in render(Session())

    def test_skipped_replays_are_listed(self) -> None:
        session = Session()
        session.skipped["b.osr"] = "the beatmap for this replay is not in the songs folder"
        page = render(session)
        assert "b.osr" in page
        assert "not in the songs folder" in page


class TestSessionTotals:
    def test_only_usable_plays_count(self) -> None:
        session = Session()
        session.add(play())
        session.add(play(agreement=Agreement.MISMATCH, errors=[500.0] * 400))
        assert len(session.usable) == 1
        assert session.mean_error == pytest.approx(0.0, abs=1.0)

    def test_key_balance_is_the_share_on_the_first_key(self) -> None:
        session = Session()
        session.add(play())
        assert session.key_balance == pytest.approx(260 / 500)


class TestAnalyse:
    def test_a_replay_whose_beatmap_is_missing_says_so(self, tmp_path: Path) -> None:
        songs = tmp_path / "songs"
        songs.mkdir()
        replay = tmp_path / "x.osr"
        from .replay_fixtures import build_replay

        replay.write_bytes(build_replay(frames=[(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)]))
        result = analyse(replay, BeatmapIndex(songs))
        assert isinstance(result, str)
        assert "not in the songs folder" in result

    def test_an_unreadable_replay_says_so(self, tmp_path: Path) -> None:
        songs = tmp_path / "songs"
        songs.mkdir()
        broken = tmp_path / "broken.osr"
        broken.write_bytes(b"")
        result = analyse(broken, BeatmapIndex(songs))
        assert isinstance(result, str)
        assert "empty" in result


class TestBeatmapIndex:
    def test_a_map_added_later_is_found_without_a_restart(self, tmp_path: Path) -> None:
        songs = tmp_path / "songs"
        songs.mkdir()
        index = BeatmapIndex(songs)
        assert len(index) == 0

        import hashlib

        path = songs / "later.osu"
        path.write_text(MINIMAL_MAP, encoding="utf-8")
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        # Downloading a map mid-session is the ordinary case, and a miss is worth
        # one rescan before giving up.
        assert index.get(digest) == path

    def test_a_hash_that_is_not_there_stays_missing(self, tmp_path: Path) -> None:
        songs = tmp_path / "songs"
        songs.mkdir()
        assert BeatmapIndex(songs).get("0" * 32) is None

    def test_a_beatmap_for_another_ruleset_does_not_break_the_index(self, tmp_path: Path) -> None:
        songs = tmp_path / "songs"
        songs.mkdir()
        (songs / "mania.osu").write_text(
            "osu file format v14\n[General]\nMode: 3\n", encoding="utf-8"
        )
        index = BeatmapIndex(songs)
        assert len(index) == 1, "it is indexed by hash; rejecting it is the parser's job"
        with pytest.raises(diffcalc.BeatmapError):
            diffcalc.Beatmap.from_file(songs / "mania.osu")
