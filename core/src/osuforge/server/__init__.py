"""The local server, and the reasons it is built the way it is.

A page that plays a replay back needs the replay, and a file rewritten every
three seconds cannot deliver forty thousand cursor samples to a renderer. So
there is a server, and because it is a server on the machine of someone who
browses the web, it is built defensively — see :mod:`osuforge.server.security`
for the four layers and for what the obvious approach gets wrong.

Nothing here reads osu!'s memory. It serves files from this package and data
derived from replays on disk, and it stops when the command that started it
stops.
"""

from osuforge.server.app import build_app
from osuforge.server.assets import Site, SiteMissingError, default_site_root, load_site
from osuforge.server.protocol import SCHEMA_VERSION, ReplayPayload
from osuforge.server.run import INTERFACE, ServerError, prepare, serve
from osuforge.server.security import DEFAULT_PORT, Access, SessionToken, runtime_file

__all__ = [
    "DEFAULT_PORT",
    "INTERFACE",
    "SCHEMA_VERSION",
    "Access",
    "ReplayPayload",
    "ServerError",
    "SessionToken",
    "Site",
    "SiteMissingError",
    "build_app",
    "default_site_root",
    "load_site",
    "prepare",
    "runtime_file",
    "serve",
]
