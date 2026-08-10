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
_POSIX_CTYPES = """
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
sys.meta_path.insert(0, _NotOnPosix())
sys.platform = "linux"

import osuforge.cli

print(osuforge.cli.__name__)
"""


def test_the_cli_imports_on_a_posix_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _POSIX_CTYPES],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"osuforge.cli did not import:\n{result.stderr}"
    assert result.stdout.strip() == "osuforge.cli"
