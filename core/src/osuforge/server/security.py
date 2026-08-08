"""Keeping a local server local.

A server bound to the loopback address is not private. Every website the user
visits can reach it, because the request leaves their own machine — so an IP
filter, which is what a local tool naturally reaches for, stops nothing that
matters. The threat is not another computer. It is a page in another tab.

This is worth being concrete about, because the best-known tool in this space
gets it wrong: tosu sets `Access-Control-Allow-Origin: *` and gates on the client
IP, which means any site the user opens can `fetch()` its API and read live game
state. See GitHub's write-up on localhost, CORS and DNS rebinding.

Four layers, each of which holds when the one above it fails:

1. **Bind `127.0.0.1` explicitly.** Not `0.0.0.0`, not `::`. A server that only
   listens on loopback cannot be reached from the network at all, which removes
   everyone except the browser.
2. **Validate the `Host` header.** DNS rebinding defeats the bind: an attacker
   points a name they control at 127.0.0.1 and the browser connects for them.
   What it cannot do is forge the `Host`, because the browser sends the name it
   resolved. Anything that is not `127.0.0.1:PORT` or `localhost:PORT` is
   refused. This is the layer that actually stops the attack.
3. **Allow-list `Origin`.** A cross-site request carries the originating site,
   and a page we served carries our own. Never `*`.
4. **A bearer token, generated per run.** Written to a discovery file so an
   overlay can find it, required on every request. It survives the first three
   being wrong, which is the only reason to have it.

The token is per run rather than stored. A token on disk from last week is a
token that leaked last week.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_PORT",
    "RESERVED_PORTS",
    "Access",
    "SessionToken",
    "runtime_file",
    "write_runtime_file",
]

DEFAULT_PORT = 24080
"""Chosen to avoid the two ports this kind of tool already uses.

tosu listens on 24050 and StreamCompanion on 20727. Colliding with either would
mean an overlay talking to the wrong program, which is worse than failing to
start.
"""

RESERVED_PORTS = frozenset({24050, 20727})
"""Ports belonging to other tools. Refused rather than silently shared."""


@dataclass(frozen=True, slots=True)
class SessionToken:
    """A secret for this run only."""

    value: str

    @classmethod
    def generate(cls) -> SessionToken:
        return cls(secrets.token_urlsafe(32))

    def matches(self, presented: str | None) -> bool:
        """Constant-time comparison.

        The timing signal on a local socket is not the likeliest attack, but the
        comparison costs nothing either way and a fast-exit `==` here is the
        kind of thing that gets quoted back later.
        """
        if not presented:
            return False
        return hmac.compare_digest(self.value, presented)


@dataclass(frozen=True, slots=True)
class Access:
    """What this run will accept."""

    port: int
    token: SessionToken

    @property
    def hosts(self) -> frozenset[str]:
        """Host headers that are this server.

        Only the loopback literal and `localhost`. A name that resolves to
        127.0.0.1 but is not one of these is a rebinding attempt: the browser
        sends the name it was given, and an attacker cannot make it send ours.
        """
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"})

    @property
    def origins(self) -> frozenset[str]:
        """Origins allowed to open a socket. Never `*`."""
        return frozenset(
            {
                f"http://127.0.0.1:{self.port}",
                f"http://localhost:{self.port}",
                f"http://[::1]:{self.port}",
            }
        )

    def host_allowed(self, host: str | None) -> bool:
        return bool(host) and host in self.hosts

    def origin_allowed(self, origin: str | None) -> bool:
        """Whether a socket may be opened from this origin.

        An absent `Origin` is allowed. A browser always sends one on a WebSocket
        handshake, so absence means a non-browser client — a script the user ran
        themselves, or OBS, whose CEF build has not been checked and is the
        reason this is not simply refused. The bearer token is what stands
        behind this decision.
        """
        if origin is None:
            return True
        return origin in self.origins


def runtime_file() -> Path:
    """Where the port and token are published for local clients to find.

    Beside the tool's own data. An overlay reads it rather than guessing a port,
    which is also what lets the port move when something else has taken it.
    """
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "runtime.json"


def write_runtime_file(access: Access, *, path: Path | None = None) -> Path:
    """Publish the port and token.

    The file holds a live credential, so the owner-only mode is set at creation
    rather than tightened afterwards — tightening leaves a window, however
    short.

    **On Windows the mode does nothing.** `os.open` accepts it and the file
    comes out `0o666`, because Windows carries only a read-only flag there and
    permissions live in an ACL. What protects it instead is where it is:
    `%LOCALAPPDATA%` sits inside the user profile, whose ACL already excludes
    other users. The mode is kept for the platforms where it is the protection.

    Either way there is a residual worth stating: a program running as this user
    can read the token, exactly as it can read anything else this user owns.
    That is inherent to publishing a credential for local clients to find, and
    it is why the token is the fourth layer rather than the only one.
    """
    target = path or runtime_file()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        {
            "schema_version": 1,
            "port": access.port,
            "token": access.token.value,
            "url": f"http://127.0.0.1:{access.port}/",
        },
        indent=2,
    )

    # Opened with O_CREAT|O_EXCL semantics via mode, then truncated, so the
    # permissions are in place before the secret is.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return target
