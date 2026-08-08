"""Redaction tests.

The central one is :meth:`TestNothingLeaks.test_sentinels_appear_in_no_rendered
_artifact`. Everything else here supports it. Users share reports and paste
configs into issues, so a credential that survives into *any* rendered output is
a real leak, not a theoretical one — and the reason redaction happens at the
parser boundary rather than in a display filter is that a display filter has to
be remembered at every output site, while a parser boundary has to be correct
once.
"""

from __future__ import annotations

import json
import re

import pytest

from osuforge.config import (
    REDACTED_PLACEHOLDER,
    EntryLine,
    OsuConfig,
    Redacted,
    RedactionPolicy,
    is_redacted,
)

from .fixtures import REALISTIC_CFG, SENTINEL_PASSWORD, SENTINEL_TOKEN, build_cfg

SENTINELS = (SENTINEL_PASSWORD, SENTINEL_TOKEN)


class TestNothingLeaks:
    def test_sentinels_appear_in_no_rendered_artifact(self) -> None:
        """The one that matters.

        Render the parsed config every way it can currently be rendered and
        assert the plaintext is in none of them. When a report or JSON exporter
        lands, add it to this list in the same commit.
        """
        cfg = OsuConfig.parse(REALISTIC_CFG)
        entries = [line for line in cfg.lines if isinstance(line, EntryLine)]
        secrets = [line.value for line in entries if isinstance(line.value, Redacted)]
        assert secrets, "fixture should contain credentials or this test proves nothing"

        artifacts: dict[str, str] = {
            "dump": cfg.dump().decode("utf-8"),
            "repr(config)": repr(cfg),
            "str(config)": str(cfg),
            "repr(lines)": repr(cfg.lines),
            "repr(entries)": repr(cfg.entries),
            "str(values)": " ".join(str(line.value) for line in entries),
            "raw(lines)": " ".join(line.raw for line in cfg.lines),
            "f-string": " ".join(f"{cfg.get(key)}" for key in cfg.entries),
            "json(redacted_keys)": json.dumps(cfg.redacted_keys()),
            "describe": " ".join(secret.describe() for secret in secrets),
        }

        for name, rendered in artifacts.items():
            for sentinel in SENTINELS:
                assert sentinel not in rendered, f"credential leaked via {name}"

    def test_dump_substitutes_the_placeholder(self) -> None:
        text = OsuConfig.parse(REALISTIC_CFG).dump().decode("utf-8")
        assert f"Password = {REDACTED_PLACEHOLDER}" in text
        assert f"Token = {REDACTED_PLACEHOLDER}" in text

    def test_plaintext_is_not_retained_anywhere_on_the_object(self) -> None:
        # Not just absent from rendering — absent from the object graph. If the
        # raw line were kept, a future serializer could reach it.
        cfg = OsuConfig.parse(REALISTIC_CFG)
        entry = cfg.entries["Password"]
        assert SENTINEL_PASSWORD not in entry.raw
        assert SENTINEL_PASSWORD not in repr(entry.value)


class TestRedactedSentinel:
    def test_every_string_conversion_returns_the_placeholder(self) -> None:
        secret = Redacted.of("hunter2")
        assert str(secret) == REDACTED_PLACEHOLDER
        assert repr(secret) == REDACTED_PLACEHOLDER
        assert f"{secret}" == REDACTED_PLACEHOLDER
        assert f"{secret:>40}" == REDACTED_PLACEHOLDER
        assert "{}".format(secret) == REDACTED_PLACEHOLDER  # noqa: UP032

    def test_fingerprint_is_stable_and_short(self) -> None:
        a = Redacted.of("hunter2")
        b = Redacted.of("hunter2")
        assert a == b
        assert len(a.fingerprint) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", a.fingerprint)

    def test_fingerprint_detects_a_changed_credential(self) -> None:
        # Enough to notice the password changed between runs; nowhere near
        # enough to recover it.
        assert Redacted.of("hunter2") != Redacted.of("hunter3")

    def test_length_is_retained_but_not_content(self) -> None:
        secret = Redacted.of("abcdefgh")
        assert secret.length == 8
        assert "abcdefgh" not in secret.describe()

    def test_describe_is_report_safe(self) -> None:
        assert Redacted.of(SENTINEL_PASSWORD).describe().startswith(REDACTED_PLACEHOLDER)
        assert SENTINEL_PASSWORD not in Redacted.of(SENTINEL_PASSWORD).describe()


class TestPolicy:
    @pytest.mark.parametrize(
        "key",
        [
            "Password",
            "Token",
            "RefreshToken",
            "AccessToken",
            "CredentialEndpoint",
            # Pattern-matched, so keys osu! has not written yet are still covered.
            "ApiKey",
            "api_key",
            "SessionCookie",
            "AuthHeader",
            "client_secret",
            "userPwd",
        ],
    )
    def test_credential_keys_are_redacted(self, key: str) -> None:
        cfg = OsuConfig.parse(build_cfg({key: "leak-me"}, header=False))
        assert is_redacted(cfg.get(key))
        assert "leak-me" not in cfg.dump().decode("utf-8")

    @pytest.mark.parametrize(
        "key",
        [
            "Offset",
            "RawInput",
            "MouseSpeed",
            "Skin",
            "Username",
            "keyOsuLeft",
            "BeatmapDirectory",
            "FrameSync",
            "CustomFrameLimit",
        ],
    )
    def test_ordinary_keys_are_not_redacted(self, key: str) -> None:
        # A false positive here would blank out a setting the rules need to read,
        # so the pattern being broad is only acceptable if it stays off these.
        cfg = OsuConfig.parse(build_cfg({key: "value"}, header=False))
        assert not is_redacted(cfg.get(key))
        assert cfg.get_str(key) == "value"

    def test_reading_a_redacted_key_as_a_string_raises(self) -> None:
        # Better a loud error than a caller silently formatting "<redacted>"
        # into a comparison and drawing a wrong conclusion from it.
        cfg = OsuConfig.parse(REALISTIC_CFG)
        with pytest.raises(ValueError, match="redacted credential"):
            cfg.get_str("Password")

    def test_redacted_keys_are_listed(self) -> None:
        assert set(OsuConfig.parse(REALISTIC_CFG).redacted_keys()) == {"Password", "Token"}

    def test_a_custom_policy_can_be_installed(self) -> None:
        policy = RedactionPolicy(exact=frozenset({"Nickname"}), pattern=re.compile(r"(?!)"))
        data = build_cfg({"Nickname": "x", "Password": "y"}, header=False)
        cfg = OsuConfig.parse(data, policy=policy)
        assert is_redacted(cfg.get("Nickname"))
        # The default exact set no longer applies, which is what makes the
        # policy a value object rather than module-level state.
        assert not is_redacted(cfg.get("Password"))

    def test_redaction_survives_a_reparse(self) -> None:
        # Parsing our own dump must not resurrect anything, and must not treat
        # the placeholder as a new secret worth hashing differently.
        first = OsuConfig.parse(REALISTIC_CFG)
        second = OsuConfig.parse(first.dump())
        assert is_redacted(second.get("Password"))
        assert SENTINEL_PASSWORD not in second.dump().decode("utf-8")
