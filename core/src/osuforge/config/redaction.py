"""Credential redaction, applied at the parser boundary.

``osu!.<user>.cfg`` contains a ``Password`` field holding a DPAPI-encrypted blob,
and osu!lazer's ``game.ini`` carries an OAuth token. Neither is something a user
should be able to leak by pasting a report into an issue — and users *do* share
reports, which is exactly why this is not a display-layer filter.

The rule: a redacted value is replaced with a :class:`Redacted` sentinel **in
memory, during parsing**. The plaintext is never stored on the parsed object, so
there is no code path — not ``dump()``, not a JSON serializer, not a stray
``repr()`` in a log line — that can reach it. Byte-exact round-tripping is
deliberately given up for redacted lines only; that trade is worth making and is
asserted in the tests rather than left implicit.

The sentinel keeps a short hash and the length so two runs can tell whether a
credential *changed* without ever knowing what it is.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final

__all__ = ["REDACTED_PLACEHOLDER", "Redacted", "RedactionPolicy"]

REDACTED_PLACEHOLDER: Final = "<redacted>"

# Exact key names known to hold credentials. osu!stable writes `Password`;
# lazer's game.ini writes `Token` and `RefreshToken`. `CredentialEndpoint` is not
# itself a secret but sits in the same family and costs nothing to cover.
_REDACT_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Password",
        "Token",
        "RefreshToken",
        "AccessToken",
        "CredentialEndpoint",
    }
)

# Catch-all for keys osu! has not written yet. Deliberately broad: a false
# positive costs one unreadable value in a report, a false negative costs a
# leaked credential.
_REDACT_PATTERN: Final = re.compile(
    r"pass|pwd|secret|token|credential|auth|session|cookie|api[_-]?key",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Redacted:
    """Stand-in for a credential value. Holds no plaintext.

    ``fingerprint`` is the first 8 hex characters of the SHA-256 of the original
    value. That is enough to detect that a credential changed between runs and
    far too little to recover it.
    """

    fingerprint: str
    length: int

    @classmethod
    def of(cls, value: str) -> Redacted:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return cls(fingerprint=digest, length=len(value))

    # Every string conversion path returns the placeholder. A credential cannot
    # reach a log, an f-string, or a JSON dump by accident, because there is no
    # conversion that yields anything else.
    def __str__(self) -> str:
        return REDACTED_PLACEHOLDER

    def __repr__(self) -> str:
        return REDACTED_PLACEHOLDER

    def __format__(self, format_spec: str) -> str:
        return REDACTED_PLACEHOLDER

    def describe(self) -> str:
        """Human-readable summary for a report. Still contains no plaintext."""
        return f"{REDACTED_PLACEHOLDER} ({self.length} chars, fp {self.fingerprint})"


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Decides which keys are credentials.

    The default covers both osu!stable and lazer. It is a value object rather
    than module-level state so tests can install a policy with sentinel keys and
    prove nothing leaks, and so a future lazer parser reuses the same object
    instead of growing a second, divergent list.
    """

    exact: frozenset[str] = _REDACT_EXACT
    pattern: re.Pattern[str] = _REDACT_PATTERN

    def should_redact(self, key: str) -> bool:
        return key in self.exact or self.pattern.search(key) is not None

    def apply(self, key: str, value: str) -> str | Redacted:
        """Return the value, or a sentinel if the key names a credential."""
        return Redacted.of(value) if self.should_redact(key) else value


def is_redacted(value: Any) -> bool:
    """Whether a parsed value is a redaction sentinel."""
    return isinstance(value, Redacted)
