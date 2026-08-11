"""The command line has to import where the browser runs.

Every system measurement osu-forge makes is a Windows API call, so the probe
modules are full of ``ctypes`` plumbing that only exists on Windows. That is
fine at run time — each probe refuses before it touches any of it — but a
module-scope ``from ctypes import wintypes`` refuses at *import* time, and the
end-to-end job that drives ``forge serve`` in a browser runs on Linux.

Nothing on Windows can regress-test that by importing. So a subprocess is given
a posix ``ctypes``: the Windows-only attributes are deleted, the Windows-only
modules are made unimportable, ``sys.platform`` says linux, and then the whole
command line is imported through it.
"""

from __future__ import annotations

import subprocess
import sys

# Deleted attributes and blocked modules together are the whole of what a posix
# CPython lacks here: `ctypes/__init__.py` defines WINFUNCTYPE, windll, oledll
# and HRESULT only under `if _os.name == "nt"`, and `ctypes.wintypes` fails on
# import because its VARIANT_BOOL type code is registered only under MS_WIN32.
#
# The lie is meant for osuforge and nothing else, so the third-party stack is
# loaded first, under the real platform: numpy reads `os.uname()` when it
# believes it is on linux, and a Windows interpreter has no such function.
#
# Loading anything first is also what makes the eviction necessary. A meta path
# finder is consulted only for names that are not already in `sys.modules`, and
# a Windows CPython imports `winreg` during startup, before a line of this
# runs, so the block on it was inert: a module-scope `import winreg` in a probe
# would have sailed through here and died on Linux. `ctypes.wintypes` is
# evicted for the same reason, and its attribute on `ctypes` deleted besides,
# because `from ctypes import wintypes` reads the attribute and never reaches a
# finder while the submodule is still loaded.
_POSIX = """
import ctypes
import sys

import numpy


class _NotOnPosix:
    def find_spec(self, name, path=None, target=None):
        if name in ("ctypes.wintypes", "winreg"):
            raise ModuleNotFoundError("No module named " + repr(name), name=name)
        return None


for attribute in ("WINFUNCTYPE", "windll", "oledll", "HRESULT"):
    delattr(ctypes, attribute)
for module in ("winreg", "ctypes.wintypes"):
    sys.modules.pop(module, None)
if hasattr(ctypes, "wintypes"):
    del ctypes.wintypes
sys.meta_path.insert(0, _NotOnPosix())
sys.platform = "linux"
"""

_IMPORT_THE_CLI = (
    _POSIX
    + """
import osuforge.cli

print(osuforge.cli.__name__)
"""
)

_TRY_THE_BLOCKED_MODULES = (
    _POSIX
    + """
for name in ("winreg", "ctypes.wintypes"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        continue
    raise AssertionError(name + " imported anyway")

print("blocked")
"""
)


def _under_posix_ctypes(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_lie_actually_blocks_the_windows_only_modules() -> None:
    # Asserted before the test below rather than assumed by it: a shim that
    # blocks nothing lets `test_the_cli_imports_on_a_posix_interpreter` pass on
    # a command line that cannot be imported off Windows at all.
    result = _under_posix_ctypes(_TRY_THE_BLOCKED_MODULES)
    assert result.returncode == 0, f"a blocked module imported anyway:\n{result.stderr}"
    assert result.stdout.strip() == "blocked"


def test_the_cli_imports_on_a_posix_interpreter() -> None:
    result = _under_posix_ctypes(_IMPORT_THE_CLI)
    assert result.returncode == 0, f"osuforge.cli did not import:\n{result.stderr}"
    assert result.stdout.strip() == "osuforge.cli"
