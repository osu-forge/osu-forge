"""Parser tests: line model, the observed quirks, and round-trip fidelity."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from osuforge.config import (
    BlankLine,
    CommentLine,
    ConfigNotFoundError,
    EntryLine,
    OsuConfig,
    RedactionPolicy,
    UnknownLine,
    find_config,
    find_install_dir,
    user_config_name,
)

from .fixtures import REALISTIC_CFG, REALISTIC_CFG_CLEAN, build_cfg


class TestObservedQuirks:
    """Each of these is a real property of a real osu!.<user>.cfg."""

    def test_mid_line_hash_in_a_value_is_not_a_comment(self) -> None:
        # `Skin = #-K- G R I S project`. A comment stripper that does not anchor
        # to column 0 eats most of this value.
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_str("Skin") == "#-K- G R I S project"

    def test_hash_at_column_zero_is_a_comment(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        comments = [line for line in cfg.lines if isinstance(line, CommentLine)]
        assert len(comments) == 3
        assert comments[0].raw.startswith("# osu! configuration file")

    def test_empty_value_is_not_a_missing_key(self) -> None:
        # `AudioDevice = ` means "system default", which is different from the
        # key being absent. The trailing space is the separator's own.
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.has("AudioDevice")
        assert cfg.get_str("AudioDevice") == ""

    def test_split_uses_only_the_first_separator(self) -> None:
        cfg = OsuConfig.parse(build_cfg({"Weird": "a = b = c"}, header=False))
        assert cfg.get_str("Weird") == "a = b = c"

    def test_value_containing_a_bare_equals_survives(self) -> None:
        # Splitting on "=" rather than " = " would truncate this.
        cfg = OsuConfig.parse(build_cfg({"Filter": "artist=Camellia"}, header=False))
        assert cfg.get_str("Filter") == "artist=Camellia"

    @pytest.mark.parametrize(
        "separator",
        ["\u2028", "\u2029", "\u0085", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"],
        ids=["LS", "PS", "NEL", "VT", "FF", "FS", "GS", "RS"],
    )
    def test_unicode_line_separators_do_not_split_a_line(self, separator: str) -> None:
        # str.splitlines() splits on every one of these. Using it here would turn
        # one entry into two lines and corrupt the file on the way back out. In
        # this format only \r\n, \r and \n terminate a line.
        value = f"before{separator}after"
        data = build_cfg({"Notes": value}, header=False)
        cfg = OsuConfig.parse(data)
        assert cfg.get_str("Notes") == value
        assert cfg.dump() == data

    def test_korean_comments_survive(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert any("자격 증명" in line.raw for line in cfg.lines)

    def test_unbound_key_is_the_literal_string_none(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_str("keyCustom1") == "None"


class TestLineClassification:
    def test_blank_lines_are_preserved(self) -> None:
        cfg = OsuConfig.parse(b"\r\n\r\nKey = Value\r\n")
        assert sum(isinstance(line, BlankLine) for line in cfg.lines) == 2

    def test_a_line_without_a_separator_is_kept_verbatim(self) -> None:
        data = b"Key = Value\r\nnot an entry at all\r\n"
        cfg = OsuConfig.parse(data)
        unknown = [line for line in cfg.lines if isinstance(line, UnknownLine)]
        assert len(unknown) == 1
        assert cfg.dump() == data

    def test_last_occurrence_of_a_duplicate_key_wins(self) -> None:
        cfg = OsuConfig.parse(b"Offset = 5\r\nOffset = 9\r\n")
        assert cfg.get_str("Offset") == "9"
        # Both lines survive, so a diff still shows the file as it really is.
        assert sum(isinstance(line, EntryLine) for line in cfg.lines) == 2

    def test_line_numbers_are_one_based(self) -> None:
        cfg = OsuConfig.parse(b"# header\r\nKey = Value\r\n")
        assert cfg.lines[0].lineno == 1
        assert cfg.lines[1].lineno == 2


class TestRoundTrip:
    """Round-trip fidelity.

    Non-redacted lines re-render from their stored ``raw``, so byte-exactness is
    true by construction for them. What these tests actually guard is the **line
    splitter** — that splitting and rejoining loses nothing, duplicates nothing,
    and never splits on a character that is legal inside a value.
    """

    def test_realistic_config_round_trips_byte_exactly(self) -> None:
        assert OsuConfig.parse(REALISTIC_CFG_CLEAN).dump() == REALISTIC_CFG_CLEAN

    def test_credential_lines_are_the_documented_exception(self) -> None:
        # A config containing credentials deliberately does NOT round-trip: the
        # redacted lines are rewritten so the plaintext is never held anywhere.
        # Everything else in the same file still round-trips untouched.
        original = REALISTIC_CFG.decode("utf-8")
        dumped = OsuConfig.parse(REALISTIC_CFG).dump().decode("utf-8")
        assert dumped != original

        differing = {
            a for a, b in zip(original.splitlines(), dumped.splitlines(), strict=True) if a != b
        }
        assert {line.split(" = ")[0] for line in differing} == {"Password", "Token"}

    @pytest.mark.parametrize("eol", ["\r\n", "\n", "\r"])
    def test_every_line_ending_round_trips(self, eol: str) -> None:
        data = build_cfg({"Offset": "0", "RawInput": "1"}, eol=eol)
        assert OsuConfig.parse(data).dump() == data

    def test_mixed_line_endings_are_preserved_per_line(self) -> None:
        data = b"A = 1\r\nB = 2\nC = 3\r"
        assert OsuConfig.parse(data).dump() == data

    def test_file_without_a_trailing_newline_round_trips(self) -> None:
        data = b"Offset = 0"
        assert OsuConfig.parse(data).dump() == data

    def test_empty_file_round_trips(self) -> None:
        assert OsuConfig.parse(b"").dump() == b""

    def test_bom_is_preserved(self) -> None:
        # osu! writes no BOM, but a hand-edited file can pick one up. Dropping it
        # on the way out would make the diff show a change nobody asked for.
        data = b"\xef\xbb\xbf" + b"Offset = 0\r\n"
        cfg = OsuConfig.parse(data)
        assert cfg.get_str("Offset") == "0"
        assert cfg.dump() == data

    def test_a_key_named_pass_is_redacted_not_round_tripped(self) -> None:
        # Found by the property test below, which generated a key literally
        # named PASS. The redaction pattern is case-insensitive and matches
        # substrings, so this is correct behaviour — a config key that looks
        # like a credential is treated as one. It is also precisely why the
        # property below has to exclude credential-matching keys: byte-exact
        # round-tripping and redaction are mutually exclusive by design.
        cfg = OsuConfig.parse(b"PASS = hunter2\r\n")
        assert cfg.dump() == b"PASS = <redacted>\r\n"

    @settings(max_examples=200)
    @given(
        lines=st.lists(
            st.one_of(
                st.tuples(
                    st.text(
                        alphabet=st.characters(
                            min_codepoint=33,
                            max_codepoint=126,
                            blacklist_characters="#=",
                        ),
                        min_size=1,
                        max_size=20,
                    ).filter(lambda key: not RedactionPolicy().should_redact(key)),
                    st.text(
                        alphabet=st.characters(
                            blacklist_categories=("Cs",), blacklist_characters="\r\n"
                        ),
                        max_size=40,
                    ),
                ).map(lambda kv: f"{kv[0]} = {kv[1]}"),
                st.text(
                    alphabet=st.characters(
                        blacklist_categories=("Cs",), blacklist_characters="\r\n"
                    ),
                    max_size=30,
                ).map(lambda s: f"#{s}"),
                st.just(""),
            ),
            max_size=25,
        ),
        eol=st.sampled_from(["\r\n", "\n", "\r"]),
    )
    def test_arbitrary_config_round_trips(self, lines: list[str], eol: str) -> None:
        # Credential-matching keys are filtered out of the generated keys above:
        # those are rewritten on purpose, so the byte-exactness property does not
        # apply to them. See test_a_key_named_pass_is_redacted_not_round_tripped.
        data = (eol.join(lines) + (eol if lines else "")).encode("utf-8")
        assert OsuConfig.parse(data).dump() == data


class TestTypedAccessors:
    def test_bool_reads_stable_zero_and_one(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_bool("RawInput") is True
        assert cfg.get_bool("AudioCompatibility") is False

    def test_bool_rejects_the_lazer_spelling(self) -> None:
        # lazer's ini files use True/False. Accepting both here would let a lazer
        # file parse as a stable one and produce silently wrong findings.
        cfg = OsuConfig.parse(b"RawInput = True\r\n")
        with pytest.raises(ValueError, match="expected 0 or 1"):
            cfg.get_bool("RawInput")

    def test_float_is_locale_invariant(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_float("CursorSize") == pytest.approx(0.64)

    def test_int_and_defaults(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_int("CustomFrameLimit") == 240
        assert cfg.get_int("NoSuchKey", default=7) == 7
        assert cfg.get_bool("NoSuchKey") is None

    def test_empty_value_falls_back_to_the_default_for_numbers(self) -> None:
        cfg = OsuConfig.parse(REALISTIC_CFG)
        assert cfg.get_int("AudioDevice", default=-1) == -1


class TestDiscovery:
    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "osu!.someone.cfg"
        path.write_bytes(REALISTIC_CFG)
        assert find_config(path) == path

    def test_missing_explicit_path_names_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigNotFoundError, match=re.escape("osu!.nobody.cfg")):
            find_config(tmp_path / "osu!.nobody.cfg")

    def test_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "osu!.env.cfg"
        path.write_bytes(REALISTIC_CFG)
        monkeypatch.setenv("OSU_FORGE_CONFIG", str(path))
        assert find_config() == path

    def test_prefers_the_current_user_config(self, fake_install: Path) -> None:
        (fake_install / "osu!.someoneelse.cfg").write_bytes(REALISTIC_CFG)
        mine = fake_install / user_config_name()
        mine.write_bytes(REALISTIC_CFG)
        assert find_config() == mine

    def test_machine_level_config_is_never_selected(self, fake_install: Path) -> None:
        # osu!.cfg holds DLL hashes and _ReleaseStream, not user settings.
        # Selecting it would yield a config with none of the expected keys.
        (fake_install / "osu!.cfg").write_bytes(b"_ReleaseStream = Stable40\r\n")
        with pytest.raises(ConfigNotFoundError):
            find_config()

    def test_falls_back_to_any_user_config(self, fake_install: Path) -> None:
        other = fake_install / "osu!.otheruser.cfg"
        other.write_bytes(REALISTIC_CFG)
        assert find_config() == other

    def test_no_install_gives_actionable_guidance(self) -> None:
        with pytest.raises(ConfigNotFoundError, match="--config"):
            find_config()

    def test_empty_directory_is_not_an_install(self, fake_install: Path) -> None:
        assert find_install_dir() is None
