#!/usr/bin/env python3
"""Strips the build-machine details from compiled Papyrus scripts, in place.

    normalize_pex.py FILE.pex...

The Creation Kit's compiler writes the compile time, the user name and the
machine name into every .pex, and the source's modification time into its
debug info. Zeroing the times and emptying the names makes a recompile of the
same source byte-identical and keeps personal details out of the repository.

Layout (big-endian; UESP "Skyrim Mod:Compiled Script File Format", checked
against the compiler's output): uint32 magic 0xFA57C0DE, uint8 major, uint8
minor, uint16 game id, uint64 compile time, wstring source file name, wstring
user name, wstring machine name; then the string table (uint16 count,
wstrings) and the debug info (uint8 present, and if nonzero a uint64
modification time, then the rest). A wstring is a uint16 length plus that many
bytes. Nothing after the header holds an absolute offset, so the rest is
copied unchanged.

All files are read and checked before any is written. Exit status: 0 on
success, 1 if a file can't be read or is not a .pex (no file is changed then),
2 on bad usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import struct
import sys

MAGIC = 0xFA57C0DE


class PexError(Exception):
    """A file that is not a .pex this script understands."""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise PexError(f"truncated at offset {self.pos:#x}")
        out = self.data[self.pos : self.pos + size]
        self.pos += size
        return out

    def unpack(self, fmt: str) -> int:
        (value,) = struct.unpack(">" + fmt, self.take(struct.calcsize(">" + fmt)))
        return value

    def wstring(self) -> bytes:
        return self.take(self.unpack("H"))


def _wstring(data: bytes) -> bytes:
    return struct.pack(">H", len(data)) + data


@dataclasses.dataclass(frozen=True)
class Header:
    """The header fields this script touches, and where the parts it copies start."""

    compile_time: int
    source: bytes
    user: bytes
    machine: bytes
    table: tuple[int, int]  # start and end of the string table
    modification_time: int | None  # None without debug info
    rest: int  # start of the part after the debug info's modification time


def read_header(data: bytes) -> Header:
    r = _Reader(data)
    magic = r.unpack("I")
    if magic != MAGIC:
        raise PexError(f"not a .pex: magic {magic:#010x}")
    r.take(4)  # major, minor, game id
    compile_time = r.unpack("Q")
    source, user, machine = r.wstring(), r.wstring(), r.wstring()
    table_start = r.pos
    for _ in range(r.unpack("H")):
        r.wstring()
    table_end = r.pos
    has_debug = r.unpack("B")
    modification_time = r.unpack("Q") if has_debug else None
    return Header(compile_time, source, user, machine, (table_start, table_end), modification_time, r.pos)


def normalize(data: bytes) -> bytes:
    """data with both times zeroed and the user and machine names emptied."""
    header = read_header(data)
    table_start, table_end = header.table
    out = data[:8] + bytes(8) + _wstring(header.source) + _wstring(b"") + _wstring(b"")
    out += data[table_start : table_end + 1]  # the string table and the debug flag
    if header.modification_time is not None:
        out += bytes(8)
    return out + data[header.rest :]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="normalize_pex.py", description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=pathlib.Path, metavar="FILE.pex")
    args = parser.parse_args(argv)

    normalized = {}
    for path in args.files:
        try:
            normalized[path] = normalize(path.read_bytes())
        except PexError as err:
            print(f"normalize_pex.py: {path}: {err}", file=sys.stderr)
            return 1
        except OSError as err:
            print(f"normalize_pex.py: {err}", file=sys.stderr)
            return 1
    for path, data in normalized.items():
        path.write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
