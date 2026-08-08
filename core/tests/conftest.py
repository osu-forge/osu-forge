"""Shared test guards.

The autouse fixture below is the important part: it points every environment
variable config discovery consults at a temporary directory, so a test cannot
read the developer's real osu! installation. Reading it would make tests pass or
fail depending on whose machine they run on, and would risk a real replay or a
real credential blob ending up in test output.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Environment variables that could route discovery at a real install.
_DISCOVERY_VARS = (
    "OSU_FORGE_CONFIG",
    "OSU_FORGE_OSU_DIR",
    "LOCALAPPDATA",
    "ProgramFiles",
    "ProgramFiles(x86)",
)


@pytest.fixture(autouse=True)
def _isolate_from_real_osu_install(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Redirect all config discovery into a throwaway directory.

    A test that wants a config puts one in here. A test that does not gets a
    clean ``ConfigNotFoundError`` instead of whatever happens to be installed.
    """
    sandbox = tmp_path_factory.mktemp("osu-sandbox")
    for var in _DISCOVERY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(sandbox))
    yield sandbox


@pytest.fixture
def fake_install(_isolate_from_real_osu_install: Path) -> Path:
    """An empty ``osu!`` directory inside the sandbox."""
    install = _isolate_from_real_osu_install / "osu!"
    install.mkdir(parents=True, exist_ok=True)
    return install


def test_sandbox_is_not_the_real_install() -> None:
    """Meta-test: the guard must actually be in force.

    If this ever fails, every other test in the suite is potentially reading the
    developer's own osu! folder.
    """
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    assert local_appdata, "LOCALAPPDATA should be redirected, not unset"
    assert not (Path(local_appdata) / "osu!" / "Songs").exists()
