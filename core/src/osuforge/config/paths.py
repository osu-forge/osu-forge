"""Locating the osu!stable installation and its per-user config.

Everything here is read-only discovery. Nothing in osu-forge writes to the osu!
folder, and these functions never open a file for writing.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "CONFIG_ENV_VAR",
    "ConfigNotFoundError",
    "find_config",
    "find_install_dir",
    "user_config_name",
]

CONFIG_ENV_VAR = "OSU_FORGE_CONFIG"
"""Overrides discovery entirely. Also how the test suite stays off the real install."""

INSTALL_ENV_VAR = "OSU_FORGE_OSU_DIR"

# The machine-level `osu!.cfg` is a different schema — DLL hashes and
# `_ReleaseStream`, not user settings. Matching it would give a config with none
# of the keys the rules expect, which is worse than finding nothing.
_MACHINE_CONFIG_NAME = "osu!.cfg"


class ConfigNotFoundError(FileNotFoundError):
    """No osu! user config could be located."""


def user_config_name(username: str | None = None) -> str:
    """``osu!.<WindowsUsername>.cfg`` — osu! names it after the OS user, not the
    osu! account name."""
    return f"osu!.{username or getpass.getuser()}.cfg"


def _candidate_install_dirs() -> Iterator[Path]:
    override = os.environ.get(INSTALL_ENV_VAR)
    if override:
        yield Path(override)
        return

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        yield Path(local_appdata) / "osu!"

    # Manual installs. osu!stable is portable, so it turns up anywhere; these are
    # only the common ones and discovery is expected to fail over to an explicit
    # --config rather than search the filesystem.
    for drive in ("C:", "D:"):
        yield Path(f"{drive}\\osu!")
    # os.environ is case-insensitive on Windows, so the canonical uppercase
    # spelling resolves the same variable the shell shows as "ProgramFiles".
    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    if program_files:
        yield Path(program_files) / "osu!"


def find_install_dir() -> Path | None:
    """First plausible osu!stable directory, or ``None``.

    A directory counts only if it contains at least one ``osu!.*.cfg``, which
    distinguishes a real install from an empty folder someone left behind.
    """
    for candidate in _candidate_install_dirs():
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        if any(candidate.glob("osu!.*.cfg")):
            return candidate
    return None


def find_config(explicit: Path | None = None) -> Path:
    """Locate the per-user config.

    Order: explicit path, then ``OSU_FORGE_CONFIG``, then
    ``<install>/osu!.<user>.cfg``, then any other ``osu!.*.cfg`` in the install
    directory — excluding the machine-level ``osu!.cfg``, which is a different
    schema.

    Raises :class:`ConfigNotFoundError` with the paths that were tried, so a
    failure tells the user what to pass to ``--config`` instead of just saying no.
    """
    tried: list[str] = []

    if explicit is not None:
        if explicit.is_file():
            return explicit
        raise ConfigNotFoundError(f"config not found at the given path: {explicit}")

    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        path = Path(from_env)
        if path.is_file():
            return path
        raise ConfigNotFoundError(f"{CONFIG_ENV_VAR} points at a missing file: {path}")

    install = find_install_dir()
    if install is None:
        raise ConfigNotFoundError(
            "could not find an osu!stable installation. Pass --config with the path "
            f"to your osu!.<username>.cfg, or set {CONFIG_ENV_VAR}."
        )

    preferred = install / user_config_name()
    tried.append(str(preferred))
    if preferred.is_file():
        return preferred

    for candidate in sorted(install.glob("osu!.*.cfg")):
        if candidate.name == _MACHINE_CONFIG_NAME:
            continue
        return candidate

    raise ConfigNotFoundError(
        f"found an osu! install at {install} but no user config in it. Tried: "
        f"{', '.join(tried)}. Pass --config explicitly."
    )
