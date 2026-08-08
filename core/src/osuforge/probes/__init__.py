"""Read-only measurements of the machine.

Every probe returns a :class:`~osuforge.probes.base.ProbeResult` and none of
them raise into the rule engine — see that module for why.
"""

from __future__ import annotations

from osuforge.probes.base import ProbeResult, ProbeStatus

__all__ = ["ProbeResult", "ProbeStatus"]
