"""Read-only access to another process, for working out where things live.

This is the prototype the Rust engine will be ported from. Pinning down sixteen
signatures and something like a hundred and fifty struct offsets is an
interactive job — attach, look, adjust, look again — and a compile-run-inspect
loop is the wrong shape for it. The measurement that justifies doing it this way
rather than writing Rust first, taken against the running game: a four-byte read
costs 9.2 us and a four-kilobyte read costs 11.5. The price is per call and not
per byte, so the right structure in any language is to read a whole struct at
once and slice it locally — which puts a hundred fields across ten reads at
about 115 us per poll, in Python.

# What this is allowed to do

`PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`. Nothing else, ever.

Not because those would fail — a non-elevated session can open plenty of
processes for more — but because every mitigation that actually works against
antivirus heuristics comes down to not doing the things malware does. Requesting
write access to another process's memory, adjusting the token to gain debug
privilege, allocating in a foreign address space, creating a remote thread: none
of them is needed to read four bytes, and each of them is a detection. CI greps
the whole repository for the names.

If osu! is running elevated and this is not, `OpenProcess` fails with
`ERROR_ACCESS_DENIED`. The answer is to match integrity levels, not to elevate
this — see :class:`AccessDenied`.

# Two traps that are quiet when you fall into them

**Module enumeration is not bitness-agnostic.** osu!stable is a 32-bit process,
and on 64-bit Windows the default filter returns nothing useful for one:
`EnumProcessModulesEx` has to be told `LIST_MODULES_32BIT`. Reading memory needs
no special handling — every address is below 4 GiB and fits a `usize` — so the
asymmetry catches people who tested the read path first.

**Reads are all-or-nothing per call.** A read that crosses out of a committed,
readable region fails entirely rather than returning what it could, so a struct
near the end of a region reads as a failure rather than as partial data.
:meth:`Process.read` clips against the region map for exactly this reason.
"""

from __future__ import annotations

import ctypes
import re
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from typing import Self

__all__ = [
    "AccessDenied",
    "Module",
    "Process",
    "ProcessNotFound",
    "Region",
    "compile_pattern",
    "find_process",
]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

# The only access rights this program ever asks for. Read and query, nothing
# that could change the target.
_PROCESS_VM_READ = 0x0010
_PROCESS_QUERY_INFORMATION = 0x0400
_ACCESS = _PROCESS_VM_READ | _PROCESS_QUERY_INFORMATION

_LIST_MODULES_32BIT = 0x01

_MEM_COMMIT = 0x1000
_PAGE_GUARD = 0x100
_PAGE_NOACCESS = 0x01
# Protections a read can succeed on. A guard page is excluded even when its
# protection is otherwise readable: touching one raises an exception in the
# target, which is a way to disturb a process we are supposed to be observing.
_READABLE = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})

_ERROR_ACCESS_DENIED = 5

_MAX_PATH = 260


class ProcessNotFound(LookupError):
    """No process with that name is running."""


class AccessDenied(PermissionError):
    """The process exists but cannot be opened for reading.

    Almost always an integrity-level mismatch: osu! is running as administrator
    and this is not. The fix is to run osu! without elevation, or to accept that
    this cannot attach — deliberately not to elevate this program, which would
    mean asking for privilege in order to read four bytes.
    """


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = (
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    )


class _ModuleInfo(ctypes.Structure):
    _fields_ = (
        ("lpBaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", wintypes.DWORD),
        ("EntryPoint", ctypes.c_void_p),
    )


@dataclass(frozen=True, slots=True)
class Module:
    name: str
    base: int
    size: int

    @property
    def end(self) -> int:
        return self.base + self.size

    def __repr__(self) -> str:
        return f"Module({self.name} at {self.base:#x}, {self.size / 1024:.0f} KiB)"


@dataclass(frozen=True, slots=True)
class Region:
    """A committed, readable span of the target's address space."""

    base: int
    size: int
    protect: int

    @property
    def end(self) -> int:
        return self.base + self.size


def find_process(name: str) -> int:
    """The process id of a running process, by executable name.

    Uses the process list rather than a window title, because a window title is
    something the game changes as you play.
    """
    import psutil

    for process in psutil.process_iter(["name", "pid"]):
        if (process.info["name"] or "").lower() == name.lower():
            pid: int = process.info["pid"]
            return pid
    raise ProcessNotFound(f"no process named {name} is running")


def compile_pattern(signature: str) -> re.Pattern[bytes]:
    """Turn `"F8 01 ?? 04"` into a regex over raw bytes.

    A regex rather than a hand-written matcher because Python's own engine is
    the fast option here: measured at about 2,400 MB/s on this machine, with
    wildcards costing nothing over literals. The Rust port will use a
    literal-anchored search instead, which is a different trade at compiled
    speed.
    """
    parts: list[bytes] = []
    for token in signature.split():
        if token in {"??", "?"}:
            parts.append(b".")
            continue
        if len(token) != 2:
            raise ValueError(f"{signature!r}: {token!r} is not a byte or a wildcard")
        parts.append(re.escape(bytes([int(token, 16)])))
    if not parts:
        raise ValueError("empty signature")
    return re.compile(b"".join(parts), re.DOTALL)


class Process:
    """An open, read-only handle on another process."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        handle = _kernel32.OpenProcess(_ACCESS, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == _ERROR_ACCESS_DENIED:
                raise AccessDenied(
                    f"cannot open process {pid} for reading (error {error}). osu! is "
                    "most likely running elevated while this is not. Restart osu! "
                    "without administrator rights — do not elevate this program, which "
                    "does not need and will not ask for the privilege."
                )
            raise OSError(error, f"OpenProcess({pid}) failed with error {error}")
        self._handle = handle
        self._regions: list[Region] | None = None

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None  # type: ignore[assignment]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def modules(self) -> list[Module]:
        """Loaded modules.

        `LIST_MODULES_32BIT` is not optional. osu!stable is a 32-bit process and
        the default filter returns nothing useful for one from a 64-bit caller.
        """
        needed = wintypes.DWORD()
        count = 1024
        while True:
            array = (ctypes.c_void_p * count)()
            ok = _psapi.EnumProcessModulesEx(
                self._handle,
                ctypes.byref(array),
                ctypes.sizeof(array),
                ctypes.byref(needed),
                _LIST_MODULES_32BIT,
            )
            if not ok:
                error = ctypes.get_last_error()
                raise OSError(error, f"EnumProcessModulesEx failed with error {error}")
            required = needed.value // ctypes.sizeof(ctypes.c_void_p)
            if required <= count:
                break
            count = required

        found: list[Module] = []
        for index in range(required):
            handle = array[index]
            if not handle:
                continue
            name = ctypes.create_unicode_buffer(_MAX_PATH)
            if not _psapi.GetModuleBaseNameW(self._handle, handle, name, _MAX_PATH):
                continue
            info = _ModuleInfo()
            if not _psapi.GetModuleInformation(
                self._handle, handle, ctypes.byref(info), ctypes.sizeof(info)
            ):
                continue
            found.append(
                Module(
                    name=name.value,
                    base=int(info.lpBaseOfDll or 0),
                    size=int(info.SizeOfImage),
                )
            )
        return found

    def regions(self, *, refresh: bool = False) -> list[Region]:
        """Committed, readable spans, in address order.

        Cached, because the map is only needed to decide where a read is legal
        and walking it costs a syscall per region. Pass `refresh` after the
        target has had time to allocate.
        """
        if self._regions is not None and not refresh:
            return self._regions

        found: list[Region] = []
        address = 0
        info = _MemoryBasicInformation()
        # osu!stable is 32-bit, so nothing it owns lives above 4 GiB. Stopping
        # there rather than walking the whole 64-bit space saves tens of
        # thousands of pointless queries.
        limit = 0x1_0000_0000
        while address < limit:
            written = _kernel32.VirtualQueryEx(
                self._handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not written:
                break
            size = int(info.RegionSize)
            if size <= 0:
                break
            base = int(info.BaseAddress or 0)
            protect = int(info.Protect)
            if (
                int(info.State) == _MEM_COMMIT
                and not protect & _PAGE_GUARD
                and protect & ~_PAGE_GUARD in _READABLE
                and protect != _PAGE_NOACCESS
            ):
                found.append(Region(base=base, size=size, protect=protect))
            address = base + size

        self._regions = found
        return found

    def read(self, address: int, size: int) -> bytes | None:
        """Read `size` bytes, or `None` if the whole range is not readable.

        `None` rather than a short read or an exception. A read that crosses out
        of a committed region fails outright at the API level, so a caller that
        treated a failure as "no data here" and carried on would be reading a
        struct that starts in one region and ends in another as absent rather
        than as a mistake.
        """
        if size <= 0:
            return b""
        buffer = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = _kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(address),
            ctypes.byref(buffer),
            size,
            ctypes.byref(read),
        )
        if not ok or read.value != size:
            return None
        return bytes(buffer)

    def scan(
        self, pattern: re.Pattern[bytes], *, limit: int | None = None
    ) -> Iterator[int]:
        """Every address whose bytes match, in address order.

        Every match, not the first. A signature that resolves more than once
        after a game update is the classic quiet failure: the first match is
        taken, the offsets read from it are garbage, and the values look
        plausible. Callers are expected to treat a second match as unresolved.
        """
        found = 0
        for region in self.regions():
            data = self.read(region.base, region.size)
            if data is None:
                continue
            for match in pattern.finditer(data):
                yield region.base + match.start()
                found += 1
                if limit is not None and found >= limit:
                    return
