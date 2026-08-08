"""Round-trip-safe parser for ``osu!.<user>.cfg``.

The file is parsed into an **ordered list of lines**, not a dict. The dict is a
derived index. That ordering matters because the point of this parser is to be
able to emit a byte-accurate diff of a proposed settings change — and osu-forge
never writes the file, so the diff *is* the deliverable.

Everything below was verified against a real 5,296-byte ``osu!.soung.cfg``
(222 lines, all CRLF, UTF-8 without BOM):

- The separator is exactly ``" = "``. Splitting on ``"="`` would destroy
  ``Skin = #-K- G R I S project`` and any value containing an equals sign, so we
  partition on the **first** ``" = "`` and nothing else.
- ``#`` starts a comment **only at column 0**. The skin name above contains one
  mid-line; a naive comment stripper eats half of it.
- An "empty value" is not a quirk. ``AudioDevice = `` is the separator's own
  trailing space with nothing after it, so ``value == ""`` round-trips by
  construction.
- Line endings are preserved per line. We never use :meth:`str.splitlines`,
  which also splits on U+2028 and U+0085 — those are legal inside a value and
  splitting on them would corrupt the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from osuforge.config.redaction import REDACTED_PLACEHOLDER, Redacted, RedactionPolicy

__all__ = [
    "BlankLine",
    "CommentLine",
    "ConfigLine",
    "EntryLine",
    "OsuConfig",
    "UnknownLine",
]

SEPARATOR = " = "
COMMENT_PREFIX = "#"


@dataclass(frozen=True, slots=True)
class ConfigLine:
    """A line of the file. ``raw`` includes the line terminator, if any."""

    raw: str
    lineno: int

    def render(self) -> str:
        return self.raw


@dataclass(frozen=True, slots=True)
class CommentLine(ConfigLine):
    """A line whose first character is ``#``."""


@dataclass(frozen=True, slots=True)
class BlankLine(ConfigLine):
    """Whitespace only."""


@dataclass(frozen=True, slots=True)
class UnknownLine(ConfigLine):
    """Non-blank, not a comment, and containing no ``" = "``.

    Preserved verbatim. osu! has never been observed writing one, but a
    corrupted or hand-edited file should survive a round trip rather than lose
    the line.
    """


@dataclass(frozen=True, slots=True)
class EntryLine(ConfigLine):
    """A ``Key = Value`` entry."""

    key: str = ""
    value: str | Redacted = ""
    terminator: str = ""

    @property
    def redacted(self) -> bool:
        return isinstance(self.value, Redacted)

    # Note there is no `render` override. For a redacted entry, `raw` is built in
    # already-redacted form during parsing, so the plaintext is not on the object
    # at all — not in `value`, not in `raw`. That is stronger than substituting
    # at render time, which leaves the secret reachable through `repr()`, a
    # dataclass serializer, or a debugger.


def _split_lines_keeping_terminators(text: str) -> list[str]:
    """Split on ``\\r\\n``, ``\\r`` or ``\\n``, keeping the terminator on each line.

    Deliberately not :meth:`str.splitlines`, which additionally splits on
    U+000B, U+000C, U+001C-U+001E, U+0085, U+2028 and U+2029. Those are valid
    inside a config value, and splitting on one silently corrupts the file.
    """
    lines: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\r":
            i += 2 if i + 1 < n and text[i + 1] == "\n" else 1
            lines.append(text[start:i])
            start = i
        elif ch == "\n":
            i += 1
            lines.append(text[start:i])
            start = i
        else:
            i += 1
    if start < n:
        lines.append(text[start:])
    return lines


def _split_terminator(raw: str) -> tuple[str, str]:
    """Split a raw line into (content, terminator)."""
    for term in ("\r\n", "\n", "\r"):
        if raw.endswith(term):
            return raw[: -len(term)], term
    return raw, ""


@dataclass(slots=True)
class OsuConfig:
    """A parsed osu! config file.

    The line list is authoritative; :attr:`entries` is a derived index. Reading a
    key that names a credential returns a :class:`Redacted` sentinel, never the
    plaintext — see :mod:`osuforge.config.redaction`.
    """

    lines: list[ConfigLine] = field(default_factory=list)
    source: Path | None = None
    encoding: str = "utf-8"
    policy: RedactionPolicy = field(default_factory=RedactionPolicy)

    @property
    def entries(self) -> dict[str, EntryLine]:
        """Key to entry. Last occurrence wins, matching osu!'s own behaviour."""
        return {line.key: line for line in self.lines if isinstance(line, EntryLine)}

    # -- parsing ----------------------------------------------------------

    @classmethod
    def parse(
        cls,
        data: bytes,
        *,
        source: Path | None = None,
        policy: RedactionPolicy | None = None,
    ) -> OsuConfig:
        """Parse raw file bytes.

        Takes bytes rather than a path or str so that the round-trip guarantee is
        testable without touching the filesystem, and so the caller cannot
        accidentally introduce a newline translation by opening in text mode.
        """
        active_policy = policy if policy is not None else RedactionPolicy()

        encoding = "utf-8"
        if data.startswith(b"\xef\xbb\xbf"):
            # osu! writes no BOM, but a hand-edited file may have picked one up.
            # Recorded so dump() can put it back.
            encoding = "utf-8-sig"
        text = data.decode(encoding)

        lines: list[ConfigLine] = []
        for index, raw in enumerate(_split_lines_keeping_terminators(text), start=1):
            content, terminator = _split_terminator(raw)

            if content.startswith(COMMENT_PREFIX):
                lines.append(CommentLine(raw=raw, lineno=index))
                continue
            if not content.strip():
                lines.append(BlankLine(raw=raw, lineno=index))
                continue

            key, separator, value = content.partition(SEPARATOR)
            if not separator:
                lines.append(UnknownLine(raw=raw, lineno=index))
                continue

            parsed_value = active_policy.apply(key, value)
            if isinstance(parsed_value, Redacted):
                # Overwrite the raw line here, at the parser boundary, so no
                # object in the graph ever holds the plaintext. This is the one
                # place byte-exact round-tripping is deliberately given up.
                raw = f"{key}{SEPARATOR}{REDACTED_PLACEHOLDER}{terminator}"

            lines.append(
                EntryLine(
                    raw=raw,
                    lineno=index,
                    key=key,
                    value=parsed_value,
                    terminator=terminator,
                )
            )

        return cls(lines=lines, source=source, encoding=encoding, policy=active_policy)

    @classmethod
    def from_path(cls, path: Path, *, policy: RedactionPolicy | None = None) -> OsuConfig:
        """Read and parse a file. Opened in binary mode, never written."""
        return cls.parse(path.read_bytes(), source=path, policy=policy)

    # -- serialization ----------------------------------------------------

    def dump(self) -> bytes:
        """Serialize back to bytes.

        Byte-identical to the input **except** for lines whose key names a
        credential, which render as ``Key = <redacted>``. That exception is
        deliberate: it means even a full dump of the parsed file cannot leak a
        password.
        """
        text = "".join(line.render() for line in self.lines)
        return text.encode(self.encoding)

    # -- reading ----------------------------------------------------------

    def get(self, key: str) -> str | Redacted | None:
        entry = self.entries.get(key)
        return None if entry is None else entry.value

    def get_str(self, key: str, default: str | None = None) -> str | None:
        value = self.get(key)
        if isinstance(value, Redacted):
            raise ValueError(f"{key} is a redacted credential and has no readable value")
        return default if value is None else value

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        """osu!stable writes booleans as ``0``/``1``.

        Note this differs from lazer's ini files, which use ``True``/``False``.
        The two must not share a parser, and this method deliberately does not
        accept the lazer spelling so that a mix-up fails loudly.
        """
        value = self.get_str(key)
        if value is None:
            return default
        stripped = value.strip()
        if stripped == "1":
            return True
        if stripped == "0":
            return False
        raise ValueError(f"{key}: expected 0 or 1 for a stable boolean, got {value!r}")

    def get_int(self, key: str, default: int | None = None) -> int | None:
        value = self.get_str(key)
        if value is None or not value.strip():
            return default
        return int(value.strip())

    def get_float(self, key: str, default: float | None = None) -> float | None:
        """Locale-invariant. ``CursorSize = 0.64`` must parse the same everywhere.

        :func:`float` is already locale-independent in Python; this method exists
        so that no caller is ever tempted to reach for :mod:`locale`.
        """
        value = self.get_str(key)
        if value is None or not value.strip():
            return default
        return float(value.strip())

    def has(self, key: str) -> bool:
        return key in self.entries

    def redacted_keys(self) -> list[str]:
        """Keys whose values were redacted. Useful for asserting the policy fired."""
        return [line.key for line in self.lines if isinstance(line, EntryLine) and line.redacted]
