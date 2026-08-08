"""The contract every subsystem speaks.

``Finding`` is the only type that crosses subsystem boundaries. The offset engine,
the input analysis, the config linter and the system probes all produce these; the
HTML report and the terminal diff printer are pure functions of them. An
import-linter contract keeps it that way.

Two design decisions here carry most of the weight:

``basis``
    Separates fact from taste. "Enhance Pointer Precision is off" is a hard fact.
    "Disable Game Mode" is disputed community preference. Rendering them
    identically would be selling taste as truth, so the field is required and the
    report styles them differently.

``confidence == INSUFFICIENT``
    Carries *no recommendation at all*. When the sample is too small the honest
    output is "not enough data, play ~15 more maps across 2 more sessions", not a
    hedged number. ``Finding.__post_init__`` enforces this rather than trusting
    each producer to remember.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

__all__ = [
    "Basis",
    "Category",
    "Confidence",
    "Evidence",
    "Finding",
    "SampleSize",
    "Severity",
    "Subsystem",
]


class Severity(StrEnum):
    """How much this matters. Ordered; see :func:`Severity.rank`."""

    CRITICAL = "critical"
    WARN = "warn"
    INFO = "info"
    PASS = "pass"
    """Checked and healthy. Worth showing so the user knows it was checked."""

    @property
    def rank(self) -> int:
        """Sort key. Lower sorts first."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.WARN: 1,
    Severity.INFO: 2,
    Severity.PASS: 3,
}


class Confidence(StrEnum):
    """How much to trust the number behind this finding."""

    INSUFFICIENT = "insufficient"
    """Below the minimum sample size. The value may be shown; no recommendation
    is permitted. Enforced in :meth:`Finding.__post_init__`."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"

    MEASURED = "measured"
    """Read directly from a registry key, a Win32 call, or a config file. Not a
    statistical estimate at all, so no interval applies."""


class Basis(StrEnum):
    """Whether this is a fact, a consensus, or an opinion.

    Required. A linter without this field inevitably presents its author's
    preferences with the same authority as measurements.
    """

    HARD_FACT = "hard_fact"
    """Verifiable from documentation or direct measurement. `FrameSync = Optimal`
    makes `CustomFrameLimit` inert — the wiki says so."""

    COMMUNITY_CONSENSUS = "community_consensus"
    """Widely agreed among players, not formally documented. Raw input on."""

    PREFERENCE = "preference"
    """Genuinely disputed. Presented as a thing to A/B test, never as a fix."""


class Subsystem(StrEnum):
    OFFSET = "offset"
    INPUT = "input"
    AIM = "aim"
    CONFIG = "config"
    SYSTEM = "system"
    DIFFCALC = "diffcalc"
    HARDWARE = "hardware"
    ENGINE = "engine"


class Category(StrEnum):
    OFFSET = "offset"
    KEYBIND = "keybind"
    INPUT = "input"
    AIM = "aim"
    FRAME = "frame"
    AUDIO = "audio"
    DISPLAY = "display"
    WINDOWS = "windows"
    SECURITY = "security"
    DATA = "data"
    """Something about the corpus itself — a corrupt replay, an unreadable database."""


@dataclass(frozen=True, slots=True)
class SampleSize:
    """Sample size at every level of nesting that matters.

    Hits are nested in replays which are nested in sessions, and hits within one
    replay share a client build, an audio device state, and a single mental
    calibration — they are anything but independent. Treating them as independent
    inflates significance enormously, so the design effect and effective sample
    size are reported rather than the raw hit count.
    """

    hits: int | None = None
    replays: int | None = None
    sessions: int | None = None

    effective: float | None = None
    """Effective sample size after deflation by the design effect."""

    design_effect: float | None = None
    """``1 + (mean_cluster_size - 1) * ICC``. Report it: with ~700 hits per replay
    and an ICC of only 0.05 this exceeds 35, and the honest interval is that much
    wider than the naive one."""

    def __str__(self) -> str:
        parts = []
        if self.hits is not None:
            parts.append(f"n={self.hits:,} hits")
        if self.replays is not None:
            parts.append(f"{self.replays} replays")
        if self.sessions is not None:
            parts.append(f"{self.sessions} sessions")
        if self.effective is not None:
            parts.append(f"n_eff≈{self.effective:,.0f}")
        return ", ".join(parts) if parts else "no sample"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One measurement supporting a finding.

    ``source`` is required and must be specific enough to audit: the exact Win32
    call, registry path, config key, or metric name. "Real refresh rate" is not a
    source; ``EnumDisplaySettingsExW(\\\\.\\DISPLAY2, ENUM_CURRENT_SETTINGS)`` is.
    Every number in the report should be traceable to where it came from.
    """

    label: str
    value: str
    source: str

    sample: SampleSize | None = None
    ci95: tuple[float, float] | None = None
    ci_method: str | None = None
    """e.g. ``"cluster-t"``, ``"hierarchical-bootstrap"``. Which method produced
    the interval changes how much it should be trusted."""

    p_value: float | None = None
    unit: str | None = None
    chart_id: str | None = None
    """Anchors this evidence to a chart in the HTML report."""

    def __post_init__(self) -> None:
        if self.ci95 is not None:
            lo, hi = self.ci95
            if not (math.isfinite(lo) and math.isfinite(hi)):
                raise ValueError(f"non-finite confidence interval: {self.ci95!r}")
            if lo > hi:
                raise ValueError(f"confidence interval is inverted: {self.ci95!r}")
            if self.ci_method is None:
                raise ValueError("ci95 requires ci_method — how it was computed matters")
        if self.p_value is not None and not 0.0 <= self.p_value <= 1.0:
            raise ValueError(f"p_value out of range: {self.p_value!r}")

    @property
    def ci_excludes_zero(self) -> bool:
        """Whether the interval excludes zero. ``False`` when there is no interval."""
        if self.ci95 is None:
            return False
        lo, hi = self.ci95
        return lo > 0.0 or hi < 0.0


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth telling the user, with the evidence behind it.

    Findings are advisory. osu-forge has no code path that writes ``osu!.cfg`` —
    ``recommended_value`` is printed as a diff and applied by the user in-game.
    """

    id: str
    """Stable slug, e.g. ``"offset.universal"``, ``"cfg.keybind.duplicate_live"``.
    Used for suppression and for cross-run tracking, so do not rename casually."""

    title: str
    summary: str
    """One sentence in plain language. This is what a user reads first."""

    severity: Severity
    confidence: Confidence
    basis: Basis
    subsystem: Subsystem
    category: Category

    rationale: str = ""
    """Why, including the causal mechanism. "Optimal ignores CustomFrameLimit"
    beats "this setting is wrong"."""

    evidence: list[Evidence] = field(default_factory=list)

    current_value: str | None = None
    recommended_value: str | None = None

    caveats: list[str] = field(default_factory=list)
    """Things that qualify the number. e.g. "roughly 8 ms of this mean is input
    sampling quantization, not hardware latency". Caveats belong next to the
    number, not in a footnote."""

    verification: str | None = None
    """What to re-check after applying, and when. A recommendation the user cannot
    verify is a recommendation they have to take on faith."""

    source_url: str | None = None
    requires_elevation: bool = False
    sample: SampleSize | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    advisory: bool = True
    """Always ``True``. Present so the report can state it, and so that any future
    attempt to add a non-advisory finding is visible in review."""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Finding.id is required")
        if not self.advisory:
            raise ValueError("findings are always advisory: osu-forge never modifies osu! files")
        if self.confidence is Confidence.INSUFFICIENT and self.recommended_value is not None:
            raise ValueError(
                f"{self.id}: cannot recommend a value at INSUFFICIENT confidence. "
                "Say what is missing and how much more data is needed instead."
            )
        if self.recommended_value is not None and self.current_value is None:
            raise ValueError(f"{self.id}: a recommendation needs the current value to diff against")

    @property
    def has_change(self) -> bool:
        """Whether this finding proposes an actual change."""
        return self.recommended_value is not None and self.recommended_value != self.current_value

    def sort_key(self) -> tuple[int, int, str]:
        """Most severe first, then most confident, then by id for stability."""
        return (self.severity.rank, -_CONFIDENCE_RANK[self.confidence], self.id)

    @classmethod
    def sorted(cls, findings: list[Self]) -> list[Self]:
        return sorted(findings, key=lambda f: f.sort_key())


_CONFIDENCE_RANK = {
    Confidence.INSUFFICIENT: 0,
    Confidence.LOW: 1,
    Confidence.MODERATE: 2,
    Confidence.HIGH: 3,
    Confidence.MEASURED: 4,
}
