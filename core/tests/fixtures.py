"""Synthetic osu! config fixtures.

Built programmatically rather than checked in, so no real config — and in
particular no real DPAPI credential blob — ever enters the repository. Every
quirk reproduced here was observed in a real 5,296-byte ``osu!.<user>.cfg``:
CRLF throughout, a ``#`` comment header, the exact ``" = "`` separator, empty
values, a skin name containing a mid-line ``#``, and unbound keys spelled
``None``.
"""

from __future__ import annotations

__all__ = [
    "REALISTIC_CFG",
    "REALISTIC_CFG_CLEAN",
    "SENTINEL_PASSWORD",
    "SENTINEL_TOKEN",
    "build_cfg",
]

# Distinctive strings that must never appear in any rendered output. Not real
# credentials and not shaped like one on purpose — a fake that looked like a
# real DPAPI blob would be a liability in a public repository, and the
# redaction logic keys on the *name*, not the value, so the shape is irrelevant
# to what is being tested.
SENTINEL_PASSWORD = "SENTINEL-PASSWORD-5f3a9c1e-MUST-NOT-LEAK"
SENTINEL_TOKEN = "SENTINEL-TOKEN-be71d40a-MUST-NOT-LEAK"


def build_cfg(entries: dict[str, str], *, header: bool = True, eol: str = "\r\n") -> bytes:
    """Render a config from ``entries``, preserving insertion order."""
    lines: list[str] = []
    if header:
        lines.append("# osu! configuration file")
        lines.append("# 마지막 업데이트: 2026년 8월 8일")
        lines.append("# 이 파일에는 자격 증명이 포함되어 있습니다")
        lines.append("")
    lines.extend(f"{key} = {value}" for key, value in entries.items())
    return (eol.join(lines) + eol).encode("utf-8")


_REALISTIC_ENTRIES: dict[str, str] = {
    # Empty value: the trailing space is the separator's own, with nothing after.
    "AudioDevice": "",
    "AudioCompatibility": "0",
    "Offset": "0",
    "VolumeUniversal": "15",
    # Mid-line '#' inside a value. A comment stripper that does not anchor to
    # column 0 destroys this.
    "Skin": "#-K- G R I S project",
    "CursorSize": "0.64",
    "MouseSpeed": "1",
    "RawInput": "1",
    "ConfineMouse": "Always",
    "FrameSync": "Optimal",
    "CustomFrameLimit": "240",
    "RefreshRate": "60",
    "OverrideRefreshRate": "0",
    "keyOsuLeft": "S",
    "keyOsuRight": "D",
    "keyOsuSmoke": "LeftShift",
    # The real conflict this project found: both bound to Space.
    "keySkip": "Space",
    "keyQuickRetry": "Space",
    "keyIncreaseAudioOffset": "OemPlus",
    # Unbound keys are spelled None and must never count as a binding conflict.
    "keyCustom1": "None",
    "keyCustom2": "None",
    "Username": "[Good Day]",
    "Language": "ko",
    "BeatmapDirectory": "Songs",
    "Password": SENTINEL_PASSWORD,
    "Token": SENTINEL_TOKEN,
}

REALISTIC_CFG: bytes = build_cfg(_REALISTIC_ENTRIES)
"""A config containing credentials. Does NOT round-trip byte-exactly — redaction
rewrites those two lines on purpose."""

REALISTIC_CFG_CLEAN: bytes = build_cfg(
    {k: v for k, v in _REALISTIC_ENTRIES.items() if k not in ("Password", "Token")}
)
"""The same config with the credential keys removed. This one must round-trip
byte-exactly, which is what the round-trip tests assert against."""
