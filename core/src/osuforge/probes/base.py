"""The probe contract.

A probe reads one fact about the machine: the real monitor refresh rate, a
registry value, the audio device period. Every probe is read-only.

The load-bearing rule is that **no probe raises into the rule engine.** Some of
what we read is unavailable without elevation, absent on some hardware, or
simply not exposed by any Windows API. A partial report that says which checks
could not run is far more useful than a stack trace, so failure is a value here,
not an exception.

Every result also carries ``source`` — the exact API call or registry path it
came from. The report prints it next to the number so a user can audit any
figure rather than taking it on faith.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["ProbeResult", "ProbeStatus"]


class ProbeStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    """Could not be read, and that is expected — needs elevation, no such
    hardware, or Windows exposes no API for it (mouse DPI, GPU latency mode)."""

    ERROR = "error"
    """Could not be read and should have been. A bug or an OS change."""


@dataclass(frozen=True, slots=True)
class ProbeResult[T]:
    id: str
    status: ProbeStatus
    source: str
    """Exact provenance. ``EnumDisplaySettingsExW(\\\\.\\DISPLAY2, ENUM_CURRENT_SETTINGS)``
    is a source; "the monitor" is not."""

    value: T | None = None
    reason: str | None = None
    requires_elevation: bool = False

    @classmethod
    def of(cls, probe_id: str, value: T, source: str) -> ProbeResult[T]:
        return cls(id=probe_id, status=ProbeStatus.OK, source=source, value=value)

    @classmethod
    def unavailable(
        cls, probe_id: str, source: str, reason: str, *, requires_elevation: bool = False
    ) -> ProbeResult[T]:
        return cls(
            id=probe_id,
            status=ProbeStatus.UNAVAILABLE,
            source=source,
            reason=reason,
            requires_elevation=requires_elevation,
        )

    @classmethod
    def error(cls, probe_id: str, source: str, reason: str) -> ProbeResult[T]:
        return cls(id=probe_id, status=ProbeStatus.ERROR, source=source, reason=reason)

    @property
    def ok(self) -> bool:
        return self.status is ProbeStatus.OK

    def require(self) -> T:
        """The value, or a clear error. Only call after checking :attr:`ok`."""
        if not self.ok or self.value is None:
            raise ValueError(f"probe {self.id} is {self.status}: {self.reason}")
        return self.value
