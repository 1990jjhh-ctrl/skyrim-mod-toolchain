#!/usr/bin/env python3
"""Fail if a plugin DLL depends on the player's Visual C++ runtime.

A plugin must not import msvcp140.dll or vcruntime140.dll. Those are the
redistributable C++ runtime, and the player's copy can be arbitrarily old. An
old msvcp140.dll in a Proton prefix cannot lock the std::mutex layout of a
newer STL, so the first lock faults and SKSE disables the plugin.

Linking the CRT statically (/MT) puts the STL implementation inside the DLL,
built by the same toolset as the headers, so they cannot drift apart. This
check keeps that true: a dependency or a preset that quietly brings back /MD
shows up here as a build failure instead of as a crash on someone else's
machine.

Exit status: 0 if no DLL imports the runtime, 1 if any does, 2 on usage error
or if any input could not be read as a PE32+ image (reported as an ERROR line).

Usage: scripts/check_runtime_deps.py <plugin.dll>...
"""

from __future__ import annotations

import pathlib
import struct
import sys

# The redistributable C++ runtime. The UCRT (api-ms-win-crt-*) is part of the
# OS on every supported Windows and is not version-skewed the same way.
FORBIDDEN_PREFIXES = ("msvcp140", "vcruntime140")


def _file_offset(data: bytes, pe_off: int, rva: int) -> int:
    """Map an RVA to a file offset through the section table."""
    (section_count,) = struct.unpack_from("<H", data, pe_off + 6)
    (opt_size,) = struct.unpack_from("<H", data, pe_off + 20)
    table = pe_off + 24 + opt_size
    for i in range(section_count):
        # IMAGE_SECTION_HEADER is 40 bytes: VirtualSize, VirtualAddress,
        # SizeOfRawData and PointerToRawData start at offset 8.
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<4I", data, table + 40 * i + 8)
        if vaddr <= rva < vaddr + max(vsize, raw_size):
            return raw_ptr + rva - vaddr
    raise ValueError(f"RVA {rva:#x} is not in any section")


def imported_dlls(path) -> list[str]:
    """Names of every DLL in the image's import directory, in table order."""
    data = pathlib.Path(path).read_bytes()
    try:
        return _parse_imports(path, data)
    except (struct.error, IndexError) as err:
        raise ValueError(f"{path}: truncated or malformed PE image") from err


def _parse_imports(path, data: bytes) -> list[str]:
    if data[:2] != b"MZ":
        raise ValueError(f"{path}: not a PE image")
    (pe_off,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise ValueError(f"{path}: not a PE image")
    opt = pe_off + 24
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic != 0x20B:
        raise ValueError(f"{path}: not a PE32+ image")
    # Data directory 1 is the import table; directories start at opt + 112 in PE32+.
    import_rva, _ = struct.unpack_from("<II", data, opt + 112 + 8)
    if import_rva == 0:
        return []

    names = []
    off = _file_offset(data, pe_off, import_rva)
    while True:
        # IMAGE_IMPORT_DESCRIPTOR is 20 bytes; Name is the RVA at offset 12.
        entry = struct.unpack_from("<5I", data, off)
        if not any(entry):
            break
        name_off = _file_offset(data, pe_off, entry[3])
        end = data.find(b"\0", name_off)
        # A name cut by the end of the file could pass for a harmless DLL.
        if name_off >= len(data) or end < 0:
            raise ValueError(f"{path}: truncated or malformed PE image")
        names.append(data[name_off:end].decode("ascii"))
        off += 20
    return names


def forbidden(names: list[str]) -> list[str]:
    return [n for n in names if n.lower().startswith(FORBIDDEN_PREFIXES)]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    failed = errored = False
    for path in argv:
        try:
            names = imported_dlls(path)
        except (OSError, ValueError) as err:
            print(f"ERROR {path}: {err}")
            errored = True
            continue
        bad = forbidden(names)
        if bad:
            print(f"FAIL {path} depends on the player's C++ runtime: {', '.join(bad)}")
            print("     Build with the static CRT (/MT); see the docstring of this script.")
            failed = True
            continue
        print(f"OK   {path}: no C++ runtime imports ({len(names)} DLLs imported)")
    if errored:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
