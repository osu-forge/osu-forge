"""Reading osu!'s config file.

Read-only by construction: nothing in this package opens a file for writing.
osu-forge recommends settings changes as a diff and the user applies them
in-game — osu! rewrites its config on exit, so a third-party process editing it
would lose the change anyway.
"""

from __future__ import annotations

from osuforge.config.parser import (
    BlankLine,
    CommentLine,
    ConfigLine,
    EntryLine,
    OsuConfig,
    UnknownLine,
)
from osuforge.config.paths import (
    CONFIG_ENV_VAR,
    ConfigNotFoundError,
    find_config,
    find_install_dir,
    user_config_name,
)
from osuforge.config.redaction import (
    REDACTED_PLACEHOLDER,
    Redacted,
    RedactionPolicy,
    is_redacted,
)

__all__ = [
    "CONFIG_ENV_VAR",
    "REDACTED_PLACEHOLDER",
    "BlankLine",
    "CommentLine",
    "ConfigLine",
    "ConfigNotFoundError",
    "EntryLine",
    "OsuConfig",
    "Redacted",
    "RedactionPolicy",
    "UnknownLine",
    "find_config",
    "find_install_dir",
    "is_redacted",
    "user_config_name",
]
