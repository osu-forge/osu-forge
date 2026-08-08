"""Reading .NET objects out of a 32-bit process.

Everything here was derived by looking at a running osu! rather than taken from
another project. See `engine/CLEANROOM.md` for the observations and the dates.

# The layout, as observed

A reference points at the method table, not at an object header. The sync-block
index lives at a negative offset and is never needed.

```
System.String, from the reference:
  +0x0  method table pointer   (4 bytes)
  +0x4  m_stringLength         (int32, characters — not bytes)
  +0x8  m_firstChar            (UTF-16LE, length * 2 bytes, not terminated)
```

# The method table is discovered, never hard-coded

It is an address in the loaded runtime, so it differs between runs and between
builds. It is found at attach time by locating a string whose contents are
already known — the username, which the configuration file supplies — and
reading what sits in front of it.

That bootstrap is worth more than the convenience. Once the value is known it is
a type check: an address whose first four bytes are not it is not a string, and
a pointer chain that has gone wrong stops producing plausible text and starts
failing. Without it, a wrong chain lands on arbitrary bytes, finds a length that
happens to be small, and returns a short piece of garbage that looks like a
name.
"""

from __future__ import annotations

import re
import struct
from collections import Counter

from prototype.memory import Process

__all__ = [
    "MAX_STRING_LENGTH",
    "StringTypeUnknownError",
    "discover_string_type",
    "read_array_length",
    "read_string",
]

MAX_STRING_LENGTH = 4096
"""Longest string this will return.

Not a limit osu! has — it is a sanity check. A pointer chain that has drifted
lands on bytes that read as an enormous length, and refusing that is the
difference between a failed read and a multi-megabyte allocation.
"""


class StringTypeUnknownError(RuntimeError):
    """The string method table could not be established.

    Raised rather than falling back to reading without a type check. Without it
    every wrong pointer produces plausible-looking text, which is the failure
    mode this whole arrangement exists to prevent.
    """


def discover_string_type(process: Process, known_text: str) -> int:
    """Find the method table value shared by every string in the process.

    `known_text` has to be something already present and already known — the
    username from the configuration file is the intended bootstrap.

    Returns the single value that fronts occurrences of that text. Raises if
    there is more than one candidate, because two would mean the thing being
    matched is not what it is assumed to be.
    """
    if not known_text:
        raise StringTypeUnknownError("no text to look for")

    needle = re.compile(re.escape(known_text.encode("utf-16-le")), re.DOTALL)
    candidates: Counter[int] = Counter()
    for region in process.regions():
        data = process.read(region.base, region.size)
        if data is None:
            continue
        for match in needle.finditer(data):
            address = region.base + match.start()
            header = process.read(address - 8, 8)
            if header is None:
                continue
            method_table, length = struct.unpack("<II", header)
            if length == len(known_text):
                candidates[method_table] += 1

    if not candidates:
        raise StringTypeUnknownError(
            f"found no object whose length field matches {known_text!r}. Either the "
            "layout is not what this assumes, or the text is not in memory."
        )
    if len(candidates) > 1:
        # Two distinct values would mean the header being matched is not a
        # method table. Guessing the most common one would work until it did
        # not, silently.
        listed = ", ".join(
            f"{value:#010x} x{count}" for value, count in candidates.most_common(4)
        )
        raise StringTypeUnknownError(
            f"{len(candidates)} candidate method tables for a string ({listed}). "
            "The layout assumption does not hold."
        )
    return next(iter(candidates))


def read_string(
    process: Process, address: int, *, string_type: int | None = None
) -> str | None:
    """Read a `System.String` from a reference, or `None` if it is not one.

    `None` covers every way this can fail — a null reference, an unreadable
    address, a method table that is not the string type, an implausible length.
    They are one outcome on purpose: a caller that has followed a wrong pointer
    should get nothing rather than something.
    """
    if not address:
        return None
    header = process.read(address, 8)
    if header is None:
        return None
    method_table, length = struct.unpack("<II", header)
    if string_type is not None and method_table != string_type:
        return None
    if length <= 0 or length > MAX_STRING_LENGTH:
        return None
    raw = process.read(address + 8, length * 2)
    if raw is None:
        return None
    # `surrogatepass` rather than `strict`: a half-read string is data to report,
    # not an exception to raise from inside a polling loop.
    return raw.decode("utf-16-le", errors="surrogatepass")


def read_array_length(process: Process, address: int) -> int | None:
    """Length of a single-dimension .NET array, from its reference.

    Same shape as a string: method table, then the count, then the elements at
    `+0x8`. The element size depends on the type and is the caller's business.
    """
    if not address:
        return None
    header = process.read(address, 8)
    if header is None:
        return None
    _, length = struct.unpack("<II", header)
    return length if 0 <= length <= 0x0010_0000 else None
