#!/usr/bin/env python3
"""Builds a Skyrim Special Edition plugin from a TOML description, and dumps one.

    esp.py build PLUGIN.toml OUT.esp
    esp.py dump FILE.esp

The TOML schema and the JSON that dump prints are documented in the README
(README.md#mod-file-tools).

build writes the same bytes for the same description: timestamps and
version-control fields are 0, records are ordered by object ID. It reads the
written file back with the same parser dump uses and fails unless that gives
the description back.

The parser is strict: it accepts only what build writes. After reading a
file it encodes the result again and rejects the file unless that gives the
same bytes, so a field it doesn't read can't hide in a dump.

The layouts follow UESP's "Skyrim Mod:Mod File Format" pages (record, group
and field headers; TES4; QUST; CELL; VMAD Field). The VMAD property layout,
the quest fields and the interior cell groups were checked against released
plugins (SkyUI_SE.esp, beyond-mastery's BeyondMastery.esp and a player home).

Exit status: 0 on success, 1 on invalid input (the message names the TOML key
or the file offset), 2 on bad usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import struct
import sys
import tomllib
from typing import Any

FORM_VERSION = 44  # Skyrim SE
HEDR_VERSION = 1.70  # as SkyUI_SE.esp; 1.71 only adds ESL object IDs below 0x800
ESL_FLAG = 0x200  # TES4 record flag

FIRST_OBJECT_ID = 0x800  # IDs below are reserved for the engine
MAX_ESL_OBJECT_ID = 0xFFF
MAX_OBJECT_ID = 0xFFFFFF
MAX_MASTERS = 253  # the plugin's own index must stay below 0xFE, the ESL space

START_GAME_ENABLED = 0x0001  # QUST DNAM flags
RUN_ONCE = 0x0100
INTERIOR = 0x0001  # CELL DATA flags

GROUP_TOP = 0
GROUP_INTERIOR_BLOCK = 2
GROUP_INTERIOR_SUB_BLOCK = 3
GROUP_HEADER_SIZE = 24

VMAD_VERSION = 5
VMAD_OBJ_FORMAT = 2  # object properties: unused uint16, alias int16, form ID
QUEST_FRAGMENTS_VERSION = 2  # "always 2"
SCRIPT_LOCAL = 0
PROPERTY_OBJECT = 1
PROPERTY_EDITED = 1
NO_ALIAS = -1

EDITOR_ID = re.compile(r"[A-Za-z0-9_]+")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # Papyrus script and property names
PLUGIN_FILE = re.compile(r"[A-Za-z0-9 _.'()\-]+\.(esm|esp|esl)", re.IGNORECASE)


class EspError(Exception):
    """Invalid input: a description or plugin file esp.py can't handle."""


@dataclasses.dataclass(frozen=True)
class FormRef:
    """A form by object ID, in a master or, when master is None, in this plugin."""

    object_id: int
    master: str | None = None


@dataclasses.dataclass(frozen=True)
class Property:
    name: str
    form: FormRef


@dataclasses.dataclass(frozen=True)
class Script:
    name: str
    properties: tuple[Property, ...] = ()


@dataclasses.dataclass(frozen=True)
class Quest:
    editor_id: str
    object_id: int
    start_game_enabled: bool = False
    run_once: bool = False
    scripts: tuple[Script, ...] = ()


@dataclasses.dataclass(frozen=True)
class Cell:
    """An empty interior cell."""

    editor_id: str
    object_id: int


@dataclasses.dataclass(frozen=True)
class Plugin:
    masters: tuple[str, ...]
    esl: bool = False
    quests: tuple[Quest, ...] = ()
    cells: tuple[Cell, ...] = ()


def canonical(plugin: Plugin) -> Plugin:
    """The plugin with its records in file order, by object ID."""
    return dataclasses.replace(
        plugin,
        quests=tuple(sorted(plugin.quests, key=lambda q: q.object_id)),
        cells=tuple(sorted(plugin.cells, key=lambda c: c.object_id)),
    )


# --- Reading the TOML description ---------------------------------------------

_MISSING = object()
_KIND_NAMES = {str: "a string", int: "an integer", bool: "true or false", list: "an array"}


def _is_kind(value: object, kind: type) -> bool:
    # bool is a subclass of int; a TOML `true` must not pass as an object ID.
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, kind)


class _Table:
    """A TOML table whose keys are taken one by one; leftover keys are errors."""

    # The values are whatever tomllib parsed; _take checks each one's type.
    def __init__(self, data: dict[str, Any], path: str) -> None:
        self.data = data
        self.path = path
        self.taken: set[str] = set()

    def key(self, name: str) -> str:
        return f"{self.path}.{name}" if self.path else name

    def take(self, name: str, kind: type, default: Any = _MISSING) -> Any:
        if name not in self.data:
            if default is _MISSING:
                raise EspError(f"{self.key(name)}: missing")
            return default
        self.taken.add(name)
        value = self.data[name]
        if not _is_kind(value, kind):
            raise EspError(f"{self.key(name)}: expected {_KIND_NAMES[kind]}")
        return value

    def strings(self, name: str) -> tuple[str, ...]:
        values = self.take(name, list)
        for index, value in enumerate(values):
            if not isinstance(value, str):
                raise EspError(f"{self.key(name)}[{index}]: expected a string")
        return tuple(values)

    def table(self, name: str) -> _Table:
        self.taken.add(name)
        value = self.data.get(name)
        if not isinstance(value, dict):
            raise EspError(f"{self.key(name)}: expected a table")
        return _Table(value, self.key(name))

    def tables(self, name: str) -> list[_Table]:
        values = self.take(name, list, [])
        tables = []
        for index, value in enumerate(values):
            path = f"{self.key(name)}[{index}]"
            if not isinstance(value, dict):
                raise EspError(f"{path}: expected a table")
            tables.append(_Table(value, path))
        return tables

    def close(self) -> None:
        unknown = sorted(set(self.data) - self.taken)
        if unknown:
            raise EspError(f"{self.key(unknown[0])}: unknown key")


def _form_from_toml(table: _Table) -> FormRef:
    form = FormRef(object_id=table.take("object_id", int), master=table.take("master", str, None))
    table.close()
    return form


def _property_from_toml(table: _Table) -> Property:
    prop = Property(name=table.take("name", str), form=_form_from_toml(table.table("form")))
    table.close()
    return prop


def _script_from_toml(table: _Table) -> Script:
    script = Script(
        name=table.take("name", str),
        properties=tuple(_property_from_toml(t) for t in table.tables("properties")),
    )
    table.close()
    return script


def _quest_from_toml(table: _Table) -> Quest:
    quest = Quest(
        editor_id=table.take("editor_id", str),
        object_id=table.take("object_id", int),
        start_game_enabled=table.take("start_game_enabled", bool, False),
        run_once=table.take("run_once", bool, False),
        scripts=tuple(_script_from_toml(t) for t in table.tables("scripts")),
    )
    table.close()
    return quest


def _cell_from_toml(table: _Table) -> Cell:
    cell = Cell(editor_id=table.take("editor_id", str), object_id=table.take("object_id", int))
    table.close()
    return cell


def load(path: pathlib.Path) -> Plugin:
    """Reads and validates a TOML description."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as err:
        raise EspError(f"not valid TOML: {err}") from err
    table = _Table(data, "")
    plugin = Plugin(
        masters=table.strings("masters"),
        esl=table.take("esl", bool, False),
        quests=tuple(_quest_from_toml(t) for t in table.tables("quests")),
        cells=tuple(_cell_from_toml(t) for t in table.tables("cells")),
    )
    table.close()
    validate(plugin)
    return plugin


# --- Validation -----------------------------------------------------------------


def _check_name(value: str, pattern: re.Pattern[str], path: str, what: str) -> None:
    if not pattern.fullmatch(value):
        raise EspError(f"{path}: {value!r} is not a valid {what}")


def _check_unique(names: list[tuple[str, str]], what: str) -> None:
    """names are (path, name) pairs; names compare case-insensitively, as in the game."""
    seen: dict[str, str] = {}
    for path, name in names:
        if name.lower() in seen:
            raise EspError(f"{path}: {what} {name!r} already used at {seen[name.lower()]}")
        seen[name.lower()] = path


def _check_form(form: FormRef, plugin: Plugin, own_ids: set[int], path: str) -> None:
    if form.master is None:
        if form.object_id not in own_ids:
            raise EspError(f"{path}.object_id: {form.object_id:#x} is no record of this plugin")
        return
    if form.master not in plugin.masters:
        raise EspError(f"{path}.master: {form.master!r} is not in masters")
    if not 0 < form.object_id <= MAX_OBJECT_ID:
        raise EspError(f"{path}.object_id: {form.object_id:#x} is out of range")


def _check_scripts(quest: Quest, path: str, plugin: Plugin, own_ids: set[int]) -> None:
    scripts = [(f"{path}.scripts[{i}].name", s.name) for i, s in enumerate(quest.scripts)]
    _check_unique(scripts, "script")
    for i, script in enumerate(quest.scripts):
        script_path = f"{path}.scripts[{i}]"
        _check_name(script.name, IDENTIFIER, f"{script_path}.name", "script name")
        names = [(f"{script_path}.properties[{j}].name", p.name) for j, p in enumerate(script.properties)]
        _check_unique(names, "property")
        for j, prop in enumerate(script.properties):
            prop_path = f"{script_path}.properties[{j}]"
            _check_name(prop.name, IDENTIFIER, f"{prop_path}.name", "property name")
            _check_form(prop.form, plugin, own_ids, f"{prop_path}.form")


def validate(plugin: Plugin) -> None:
    """Raises EspError, naming the TOML key, if build can't write the plugin."""
    if len(plugin.masters) > MAX_MASTERS:
        raise EspError(f"masters: more than {MAX_MASTERS}")
    for i, master in enumerate(plugin.masters):
        _check_name(master, PLUGIN_FILE, f"masters[{i}]", "plugin file name")
    _check_unique([(f"masters[{i}]", m) for i, m in enumerate(plugin.masters)], "master")

    records = [(f"quests[{i}]", q) for i, q in enumerate(plugin.quests)]
    records += [(f"cells[{i}]", c) for i, c in enumerate(plugin.cells)]
    max_id = MAX_ESL_OBJECT_ID if plugin.esl else MAX_OBJECT_ID
    for path, record in records:
        _check_name(record.editor_id, EDITOR_ID, f"{path}.editor_id", "editor ID")
        if not FIRST_OBJECT_ID <= record.object_id <= max_id:
            raise EspError(
                f"{path}.object_id: {record.object_id:#x} is outside {FIRST_OBJECT_ID:#x}..{max_id:#x}"
            )
    _check_unique([(f"{p}.editor_id", r.editor_id) for p, r in records], "editor ID")
    _check_unique([(f"{p}.object_id", f"{r.object_id:#x}") for p, r in records], "object ID")

    own_ids = {record.object_id for _, record in records}
    for i, quest in enumerate(plugin.quests):
        _check_scripts(quest, f"quests[{i}]", plugin, own_ids)


# --- Writing --------------------------------------------------------------------


def _own_index(plugin: Plugin) -> int:
    """The mod index of this plugin's own records: the number of masters."""
    return len(plugin.masters)


def _form_id(plugin: Plugin, form: FormRef) -> int:
    index = _own_index(plugin) if form.master is None else plugin.masters.index(form.master)
    return index << 24 | form.object_id


def _zstring(text: str) -> bytes:
    return text.encode("ascii") + b"\0"


def _wstring(text: str) -> bytes:
    data = text.encode("ascii")
    return struct.pack("<H", len(data)) + data


def _field(kind: bytes, data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise EspError(f"{kind.decode()} field too large: {len(data)} bytes")
    return kind + struct.pack("<H", len(data)) + data


def _record(kind: bytes, flags: int, form_id: int, fields: list[bytes]) -> bytes:
    data = b"".join(fields)
    # size, flags, form ID, timestamp, version control, form version, unknown
    header = struct.pack("<IIIHHHH", len(data), flags, form_id, 0, 0, FORM_VERSION, 0)
    return kind + header + data


def _group(label: bytes, group_type: int, children: list[bytes]) -> bytes:
    data = b"".join(children)
    # size (with this header), label, type, timestamp, version control, unknown
    header = struct.pack("<I4siHHI", GROUP_HEADER_SIZE + len(data), label, group_type, 0, 0, 0)
    return b"GRUP" + header + data


def _vmad(plugin: Plugin, quest: Quest) -> bytes:
    out = struct.pack("<hhH", VMAD_VERSION, VMAD_OBJ_FORMAT, len(quest.scripts))
    for script in quest.scripts:
        out += _wstring(script.name) + struct.pack("<BH", SCRIPT_LOCAL, len(script.properties))
        for prop in script.properties:
            out += _wstring(prop.name) + struct.pack("<BB", PROPERTY_OBJECT, PROPERTY_EDITED)
            out += struct.pack("<HhI", 0, NO_ALIAS, _form_id(plugin, prop.form))
    # Quest fragments: version, no fragments, no fragment script, no aliases.
    return out + struct.pack("<bH", QUEST_FRAGMENTS_VERSION, 0) + _wstring("") + struct.pack("<H", 0)


def _quest(plugin: Plugin, quest: Quest) -> bytes:
    flags = (START_GAME_ENABLED if quest.start_game_enabled else 0) | (RUN_ONCE if quest.run_once else 0)
    fields = [_field(b"EDID", _zstring(quest.editor_id))]
    if quest.scripts:
        fields.append(_field(b"VMAD", _vmad(plugin, quest)))
    fields += [
        # flags, priority, unknown, unknown int32, quest type 0 (none)
        _field(b"DNAM", struct.pack("<HBBiI", flags, 0, 0, 0, 0)),
        _field(b"NEXT", b""),
        _field(b"ANAM", struct.pack("<I", 0)),  # next alias ID: no aliases
    ]
    return _record(b"QUST", 0, _form_id(plugin, FormRef(quest.object_id)), fields)


def cell_block(object_id: int) -> tuple[int, int]:
    """The interior block and sub-block: the last and second-last decimal digits."""
    return object_id % 10, object_id // 10 % 10


def _cell(plugin: Plugin, cell: Cell) -> bytes:
    fields = [_field(b"EDID", _zstring(cell.editor_id)), _field(b"DATA", struct.pack("<H", INTERIOR))]
    return _record(b"CELL", 0, _form_id(plugin, FormRef(cell.object_id)), fields)


def _cell_group(plugin: Plugin) -> tuple[bytes, int]:
    """The top CELL group and how many groups and records it holds, itself included."""
    blocks: dict[int, dict[int, list[Cell]]] = {}
    for cell in plugin.cells:
        block, sub_block = cell_block(cell.object_id)
        blocks.setdefault(block, {}).setdefault(sub_block, []).append(cell)
    count = 1 + len(plugin.cells)
    block_groups = []
    for block, sub_blocks in sorted(blocks.items()):
        sub_groups = []
        for sub_block, cells in sorted(sub_blocks.items()):
            records = [_cell(plugin, c) for c in sorted(cells, key=lambda c: c.object_id)]
            sub_groups.append(_group(struct.pack("<i", sub_block), GROUP_INTERIOR_SUB_BLOCK, records))
        block_groups.append(_group(struct.pack("<i", block), GROUP_INTERIOR_BLOCK, sub_groups))
        count += 1 + len(sub_groups)
    return _group(b"CELL", GROUP_TOP, block_groups), count


def _header(plugin: Plugin, num_records: int) -> bytes:
    ids = [r.object_id for r in (*plugin.quests, *plugin.cells)]
    next_object_id = max(ids, default=FIRST_OBJECT_ID - 1) + 1
    fields = [_field(b"HEDR", struct.pack("<fII", HEDR_VERSION, num_records, next_object_id))]
    for master in plugin.masters:
        fields += [_field(b"MAST", _zstring(master)), _field(b"DATA", struct.pack("<Q", 0))]
    fields.append(_field(b"INTV", struct.pack("<I", 1)))
    return _record(b"TES4", ESL_FLAG if plugin.esl else 0, 0, fields)


def encode(plugin: Plugin) -> bytes:
    """The plugin's bytes. The plugin must have passed validate()."""
    plugin = canonical(plugin)
    groups = []
    num_records = 0  # groups count as records here
    if plugin.cells:
        group, count = _cell_group(plugin)
        groups.append(group)
        num_records += count
    if plugin.quests:
        groups.append(_group(b"QUST", GROUP_TOP, [_quest(plugin, q) for q in plugin.quests]))
        num_records += 1 + len(plugin.quests)
    return _header(plugin, num_records) + b"".join(groups)


# --- Reading a plugin -----------------------------------------------------------


class _Reader:
    """Little-endian reads over data[pos:end] that report absolute file offsets."""

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None) -> None:
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def fail(self, message: str, offset: int | None = None) -> EspError:
        return EspError(f"offset {self.pos if offset is None else offset:#x}: {message}")

    def take(self, size: int) -> bytes:
        if self.pos + size > self.end:
            raise self.fail(f"truncated, {size} bytes needed and {self.end - self.pos} left")
        out = self.data[self.pos : self.pos + size]
        self.pos += size
        return out

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        # struct.unpack returns a tuple of mixed types.
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def sub(self, size: int) -> _Reader:
        start = self.pos
        self.take(size)
        return _Reader(self.data, start, start + size)

    def text(self, size: int) -> str:
        start = self.pos
        try:
            return self.take(size).decode("ascii")
        except UnicodeDecodeError as err:
            raise self.fail("text is not ASCII", start) from err

    def wstring(self) -> str:
        (size,) = self.unpack("<H")
        return self.text(size)

    def zstring(self) -> str:
        start = self.pos
        text = self.text(self.end - self.pos)
        if not text.endswith("\0") or "\0" in text[:-1]:
            raise self.fail("not a zero-terminated string", start)
        return text[:-1]

    def at_end(self) -> bool:
        return self.pos >= self.end


@dataclasses.dataclass
class _Record:
    flags: int
    form_id: int
    fields: list[tuple[bytes, _Reader]]

    def field(self, name: bytes, r: _Reader, offset: int) -> _Reader:
        for kind, data in self.fields:
            if kind == name:
                return data
        raise r.fail(f"record has no {name.decode()} field", offset)

    def has(self, name: bytes) -> bool:
        return any(kind == name for kind, _ in self.fields)


def _read_record(r: _Reader, kind: bytes) -> _Record:
    start = r.pos
    found = r.take(4)
    if found != kind:
        raise r.fail(f"expected a {kind.decode()} record, found {found!r}", start)
    size, flags, form_id, *_ = r.unpack("<IIIHHHH")
    body = r.sub(size)
    fields = []
    while not body.at_end():
        name = body.take(4)
        (field_size,) = body.unpack("<H")
        fields.append((name, body.sub(field_size)))
    return _Record(flags, form_id, fields)


def _read_group(r: _Reader, label: bytes | int, group_type: int) -> _Reader:
    start = r.pos
    if r.take(4) != b"GRUP":
        raise r.fail("expected a group", start)
    size, found_label, found_type, *_ = r.unpack("<I4siHHI")
    if isinstance(label, int):
        (found_label,) = struct.unpack("<i", found_label)
    if (found_label, found_type) != (label, group_type):
        raise r.fail(f"expected group {label!r} of type {group_type}", start)
    if size < GROUP_HEADER_SIZE:
        raise r.fail("group smaller than its header", start)
    return r.sub(size - GROUP_HEADER_SIZE)


def _peek_group(r: _Reader) -> tuple[bytes, int]:
    """The label and type of the group at the reader's position."""
    start = r.pos
    if r.take(4) != b"GRUP":
        raise r.fail("expected a group", start)
    _, label, group_type, *_ = r.unpack("<I4siHHI")
    r.pos = start
    return label, group_type


def _form_ref(form_id: int, masters: tuple[str, ...], r: _Reader, offset: int) -> FormRef:
    index, object_id = form_id >> 24, form_id & MAX_OBJECT_ID
    if index == len(masters):
        return FormRef(object_id)
    if index > len(masters):
        raise r.fail(f"form ID {form_id:#010x} names no master", offset)
    return FormRef(object_id, masters[index])


def _own_object_id(record: _Record, masters: tuple[str, ...], r: _Reader, offset: int) -> int:
    if record.form_id >> 24 != len(masters):
        raise r.fail(f"form ID {record.form_id:#010x} is not this plugin's own", offset)
    return record.form_id & MAX_OBJECT_ID


def _read_property(r: _Reader, masters: tuple[str, ...]) -> Property:
    start = r.pos
    name = r.wstring()
    prop_type, _ = r.unpack("<BB")
    if prop_type != PROPERTY_OBJECT:
        raise r.fail(f"property {name}: type {prop_type}, only objects are supported", start)
    *_, form_id = r.unpack("<HhI")
    return Property(name, _form_ref(form_id, masters, r, start))


def _read_vmad(r: _Reader, masters: tuple[str, ...]) -> tuple[Script, ...]:
    start = r.pos
    version, obj_format, count = r.unpack("<hhH")
    if (version, obj_format) != (VMAD_VERSION, VMAD_OBJ_FORMAT):
        raise r.fail(f"VMAD version {version} object format {obj_format}", start)
    scripts = []
    for _ in range(count):
        name = r.wstring()
        _, property_count = r.unpack("<BH")
        properties = tuple(_read_property(r, masters) for _ in range(property_count))
        scripts.append(Script(name, properties))
    return tuple(scripts)


def _read_quest(r: _Reader, masters: tuple[str, ...]) -> Quest:
    start = r.pos
    record = _read_record(r, b"QUST")
    scripts = ()
    if record.has(b"VMAD"):
        scripts = _read_vmad(record.field(b"VMAD", r, start), masters)
    (flags,) = record.field(b"DNAM", r, start).unpack("<H")
    return Quest(
        editor_id=record.field(b"EDID", r, start).zstring(),
        object_id=_own_object_id(record, masters, r, start),
        start_game_enabled=bool(flags & START_GAME_ENABLED),
        run_once=bool(flags & RUN_ONCE),
        scripts=scripts,
    )


def _read_cell(r: _Reader, masters: tuple[str, ...]) -> Cell:
    start = r.pos
    record = _read_record(r, b"CELL")
    editor_id = record.field(b"EDID", r, start).zstring()
    return Cell(editor_id, _own_object_id(record, masters, r, start))


def _read_block(r: _Reader, group_type: int) -> _Reader:
    """The body of the interior block or sub-block group at the reader's position."""
    label, _ = _peek_group(r)
    (number,) = struct.unpack("<i", label)
    return _read_group(r, number, group_type)


def _read_cells(group: _Reader, masters: tuple[str, ...]) -> list[Cell]:
    cells = []
    while not group.at_end():
        block = _read_block(group, GROUP_INTERIOR_BLOCK)
        while not block.at_end():
            sub_block = _read_block(block, GROUP_INTERIOR_SUB_BLOCK)
            while not sub_block.at_end():
                cells.append(_read_cell(sub_block, masters))
    return cells


def _read_header(r: _Reader) -> tuple[tuple[str, ...], bool]:
    record = _read_record(r, b"TES4")
    masters = tuple(data.zstring() for kind, data in record.fields if kind == b"MAST")
    return masters, bool(record.flags & ESL_FLAG)


def _first_difference(a: bytes, b: bytes) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def parse(data: bytes) -> Plugin:
    """Reads a plugin build could have written. Raises EspError on anything else."""
    r = _Reader(data)
    masters, esl = _read_header(r)
    quests: list[Quest] = []
    cells: list[Cell] = []
    if not r.at_end() and _peek_group(r)[0] == b"CELL":
        cells = _read_cells(_read_group(r, b"CELL", GROUP_TOP), masters)
    if not r.at_end():
        group = _read_group(r, b"QUST", GROUP_TOP)
        while not group.at_end():
            quests.append(_read_quest(group, masters))
    if not r.at_end():
        raise r.fail("unexpected data after the last group")

    plugin = canonical(Plugin(masters, esl, tuple(quests), tuple(cells)))
    try:
        validate(plugin)
    except EspError as err:
        raise EspError(f"a record esp.py can't write: {err}") from err
    expected = encode(plugin)
    if expected != data:
        offset = _first_difference(expected, data)
        raise EspError(f"offset {offset:#x}: not in the layout esp.py build writes")
    return plugin


# --- Command line ---------------------------------------------------------------


def to_json(plugin: Plugin) -> dict[str, Any]:
    """The plugin as the JSON dump prints it: the TOML schema's shape."""
    # asdict gives plain values; a form in this plugin has no master key, as in TOML.
    data = dataclasses.asdict(plugin)
    forms = [
        prop["form"]
        for quest in data["quests"]
        for script in quest["scripts"]
        for prop in script["properties"]
    ]
    for form in forms:
        if form["master"] is None:
            del form["master"]
    return data


def build(source: pathlib.Path, out: pathlib.Path) -> None:
    """Writes the plugin, reads it back and keeps it only if it gives the source back."""
    plugin = canonical(load(source))
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(encode(plugin))
        if parse(tmp.read_bytes()) != plugin:
            raise EspError(f"{out}: read back differs from {source}")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="esp.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build", help="build an .esp from a TOML description")
    build_command.add_argument("source", type=pathlib.Path, help="the TOML description")
    build_command.add_argument("out", type=pathlib.Path, help="the .esp to write")
    dump_command = commands.add_parser("dump", help="print an .esp as JSON")
    dump_command.add_argument("plugin", type=pathlib.Path, help="the .esp to read")
    args = parser.parse_args(argv)

    path = args.source if args.command == "build" else args.plugin
    try:
        if args.command == "build":
            build(args.source, args.out)
        else:
            print(json.dumps(to_json(parse(args.plugin.read_bytes())), indent=2))
    except EspError as err:
        print(f"esp.py: {path}: {err}", file=sys.stderr)
        return 1
    except OSError as err:
        print(f"esp.py: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
