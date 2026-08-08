"""Keybind conflict detection.

The parametrized matrix below is the most important test in this file, and the
**true-negatives** are the reason. A naive duplicate check reports four
collisions on a real, healthy config; only one is real. A detector that cries
wolf gets ignored, and then it is worth nothing when it is right.
"""

from __future__ import annotations

import pytest

from osuforge.config import OsuConfig
from osuforge.config.keybinds import (
    KeyScope,
    find_conflicts,
    read_bindings,
    scopes_for,
)

from .fixtures import REALISTIC_CFG, build_cfg


def _cfg(**bindings: str) -> OsuConfig:
    return OsuConfig.parse(build_cfg(dict(bindings), header=False))


class TestConflictMatrix:
    """Every case verified against a real osu!.<user>.cfg."""

    @pytest.mark.parametrize(
        ("bindings", "expect_conflict", "why"),
        [
            (
                {"keySkip": "Space", "keyQuickRetry": "Space"},
                True,
                "both are live during gameplay in every ruleset",
            ),
            (
                {"keyOsuSmoke": "LeftShift", "keyFruitsDash": "LeftShift"},
                False,
                "osu! and catch gameplay never run at the same time",
            ),
            (
                {"keyOsuLeft": "S", "keySuddenDeath": "S"},
                False,
                "gameplay versus the mod select screen",
            ),
            (
                {"keyTaikoOuterLeft": "Z", "keyJumpToBegin": "Z"},
                False,
                "taiko gameplay versus the editor",
            ),
            (
                {"keyCustomA": "None", "keyCustomB": "None"},
                False,
                "two unbound keys are not a conflict",
            ),
            (
                {"keyOsuLeft": "S", "keyOsuRight": "S"},
                True,
                "same ruleset, same screen",
            ),
            (
                {"keyScreenshot": "F12", "keyOsuLeft": "F12"},
                True,
                "a global binding is live during gameplay too",
            ),
            (
                {"keyTaikoInnerLeft": "X", "keyTaikoOuterLeft": "X"},
                True,
                "both live in taiko gameplay",
            ),
            (
                {"keyEasy": "Q", "keyHardRock": "Q"},
                True,
                "both live on the mod select screen",
            ),
            (
                {"keyOsuLeft": "Z", "keyTaikoOuterLeft": "Z"},
                False,
                "different rulesets",
            ),
        ],
        ids=[
            "skip-vs-quickretry-CONFLICT",
            "smoke-vs-fruitsdash-ok",
            "osuleft-vs-suddendeath-ok",
            "taiko-vs-editor-ok",
            "both-unbound-ok",
            "osu-left-vs-right-CONFLICT",
            "global-vs-gameplay-CONFLICT",
            "taiko-inner-vs-outer-CONFLICT",
            "modselect-pair-CONFLICT",
            "osu-vs-taiko-ok",
        ],
    )
    def test_matrix(self, bindings: dict[str, str], expect_conflict: bool, why: str) -> None:
        conflicts = find_conflicts(_cfg(**bindings))
        assert bool(conflicts) is expect_conflict, why

    def test_real_config_yields_exactly_one_conflict(self) -> None:
        # The fixture reproduces a real config, which contains both the genuine
        # Space collision and the LeftShift false-positive bait.
        conflicts = find_conflicts(OsuConfig.parse(REALISTIC_CFG))
        assert len(conflicts) == 1
        assert set(conflicts[0].keys) == {"keySkip", "keyQuickRetry"}
        assert conflicts[0].value == "Space"


class TestUnknownKeysAreNotGuessed:
    def test_unrecognised_key_has_no_scope(self) -> None:
        assert scopes_for("keySomethingNobodyHasSeen") is None

    def test_unrecognised_keys_never_produce_a_conflict(self) -> None:
        # A missed conflict is a gap. An invented one destroys trust in every
        # other finding, so unknown scope means "do not reason about this".
        assert find_conflicts(_cfg(keyMysteryA="J", keyMysteryB="J")) == []

    def test_a_known_key_does_not_conflict_with_an_unknown_one(self) -> None:
        assert find_conflicts(_cfg(keySkip="J", keyMystery="J")) == []


class TestScopeTable:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("keySkip", 4),
            ("keyScreenshot", len(KeyScope)),
            ("keyOsuLeft", 1),
            ("keyMania4K2", 1),
            ("keyDoubleTime", 1),
            ("keyNudgeLeft", 1),
        ],
    )
    def test_scope_counts(self, key: str, expected: int) -> None:
        scopes = scopes_for(key)
        assert scopes is not None
        assert len(scopes) == expected

    def test_mania_bindings_are_pattern_matched(self) -> None:
        # osu! only writes mania bindings once mania has been configured, so a
        # parser must treat them as absent rather than defaulted.
        assert scopes_for("keyMania7K3") == frozenset({KeyScope.PLAY_MANIA})
        assert scopes_for("keyMania1K1") == frozenset({KeyScope.PLAY_MANIA})


class TestReadBindings:
    def test_only_key_prefixed_entries_are_bindings(self) -> None:
        bindings = read_bindings(OsuConfig.parse(REALISTIC_CFG))
        assert all(b.key.startswith("key") for b in bindings)
        assert not any(b.key == "Offset" for b in bindings)

    def test_unbound_is_the_literal_string_none(self) -> None:
        bindings = {b.key: b for b in read_bindings(OsuConfig.parse(REALISTIC_CFG))}
        assert bindings["keyCustom1"].value == "None"
        assert not bindings["keyCustom1"].bound
        assert bindings["keyOsuLeft"].bound

    def test_line_numbers_are_carried_through(self) -> None:
        bindings = {b.key: b for b in read_bindings(OsuConfig.parse(REALISTIC_CFG))}
        assert bindings["keySkip"].lineno > 0


class TestGrouping:
    def test_three_keys_on_one_value_report_as_one_conflict(self) -> None:
        conflicts = find_conflicts(_cfg(keySkip="Space", keyQuickRetry="Space", keyPause="Space"))
        assert len(conflicts) == 1
        assert len(conflicts[0].keys) == 3

    def test_a_non_overlapping_key_is_left_out_of_the_group(self) -> None:
        # keyOsuLeft and keyOsuRight conflict. keyTaikoInnerLeft shares the value
        # but overlaps neither, so it must not be dragged into their conflict.
        conflicts = find_conflicts(_cfg(keyOsuLeft="X", keyOsuRight="X", keyTaikoInnerLeft="X"))
        assert len(conflicts) == 1
        assert set(conflicts[0].keys) == {"keyOsuLeft", "keyOsuRight"}

    def test_a_gameplay_wide_key_does_overlap_a_single_ruleset_key(self) -> None:
        # keySkip is live in every ruleset, so it genuinely collides with a
        # catch-only binding. Easy to get backwards: the ruleset scoping only
        # rescues keys that are both ruleset-specific.
        conflicts = find_conflicts(_cfg(keySkip="LeftShift", keyFruitsDash="LeftShift"))
        assert len(conflicts) == 1
        assert set(conflicts[0].keys) == {"keySkip", "keyFruitsDash"}
        assert conflicts[0].scopes == frozenset({KeyScope.PLAY_FRUITS})

    def test_describe_names_the_keys_and_the_contexts(self) -> None:
        conflict = find_conflicts(_cfg(keySkip="Space", keyQuickRetry="Space"))[0]
        described = conflict.describe()
        assert "keySkip" in described
        assert "keyQuickRetry" in described
        assert "Space" in described
