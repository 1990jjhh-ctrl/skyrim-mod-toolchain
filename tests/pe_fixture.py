"""Builds a minimal PE image whose import table the runtime check can walk."""

import struct

PE_OFFSET = 0x40
OPTIONAL_HEADER_SIZE = 240
IMPORT_DIRECTORY_OFFSET = 120  # data directory 1, from the optional header start
SECTION_RVA = 0x1000
SECTION_SIZE = 0x200
SECTION_FILE_OFFSET = 0x200
DESCRIPTOR_SIZE = 20


def build_pe(imports: list[str], *, magic: int = 0x20B) -> bytes:
    """Return a PE image importing the given DLL names, in order."""
    image = bytearray(SECTION_FILE_OFFSET + SECTION_SIZE)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, PE_OFFSET)
    image[PE_OFFSET:PE_OFFSET + 4] = b"PE\0\0"

    coff = PE_OFFSET + 4
    struct.pack_into("<H", image, coff + 2, 1)  # NumberOfSections
    struct.pack_into("<H", image, coff + 16, OPTIONAL_HEADER_SIZE)

    optional = coff + 20
    struct.pack_into("<H", image, optional, magic)
    if imports:
        struct.pack_into("<I", image, optional + IMPORT_DIRECTORY_OFFSET, SECTION_RVA)

    section = optional + OPTIONAL_HEADER_SIZE
    struct.pack_into(
        "<4I",
        image,
        section + 8,
        SECTION_SIZE,  # VirtualSize
        SECTION_RVA,  # VirtualAddress
        SECTION_SIZE,  # SizeOfRawData
        SECTION_FILE_OFFSET,  # PointerToRawData
    )

    # Descriptors first (plus the zero terminator), then the names.
    name_offset = SECTION_FILE_OFFSET + DESCRIPTOR_SIZE * (len(imports) + 1)
    for i, name in enumerate(imports):
        name_rva = SECTION_RVA + name_offset - SECTION_FILE_OFFSET
        struct.pack_into("<I", image, SECTION_FILE_OFFSET + DESCRIPTOR_SIZE * i + 12, name_rva)
        encoded = name.encode("ascii") + b"\0"
        image[name_offset:name_offset + len(encoded)] = encoded
        name_offset += len(encoded)
    return bytes(image)
