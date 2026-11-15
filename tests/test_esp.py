"""Tests for scripts/esp.py, the plugin writer. The game is the only real check
of a plugin; these tests pin the layouts to bytes worked out by hand and taken
from released plugins, and check that build and dump agree."""

import importlib.util
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest

from tests.support import TOOLCHAIN_ROOT

SCRIPT = TOOLCHAIN_ROOT / "scripts" / "esp.py"


def _load():
    spec = importlib.util.spec_from_file_location("esp", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


esp = _load()

SAMPLE = """\
masters = ["Skyrim.esm", "Update.esm"]

[[quests]]
editor_id = "SampleQuest"
object_id = 0x800
start_game_enabled = true
run_once = true

[[quests.scripts]]
name = "SampleQuestScript"

[[quests.scripts.properties]]
name = "Reward"
form = { master = "Skyrim.esm", object_id = 0x00000F }

[[quests.scripts.properties]]
name = "TestCell"
form = { object_id = 0x801 }

[[quests.scripts]]
name = "SecondScript"

[[cells]]
editor_id = "SampleCell"
object_id = 0x801
"""

# beyond-mastery's MCM plugin: a quest of the same shape as SkyUI_SE.esp's
# SKI_MainInstance, whose alias 0 is forced to the player and runs SkyUI's
# SKI_PlayerLoadGameAlias.
MCM = """\
masters = ["Skyrim.esm"]
esl = true

[[quests]]
editor_id = "BeyondMasteryMCM"
object_id = 0x800
start_game_enabled = true
run_once = true

[[quests.scripts]]
name = "BeyondMasteryMCM"

[[quests.aliases]]
name = "PlayerAlias"
forced_ref = { master = "Skyrim.esm", object_id = 0x14 }

[[quests.aliases.scripts]]
name = "SKI_PlayerLoadGameAlias"
"""


def _load_text(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "plugin.toml"
        path.write_text(text)
        return esp.load(path)


def _field_names(record: bytes) -> list[bytes]:
    names, pos = [], 24
    while pos < len(record):
        names.append(record[pos : pos + 4])
        pos += 6 + struct.unpack_from("<H", record, pos + 4)[0]
    return names


def _field(record: bytes, name: bytes) -> bytes:
    """The data of the first field `name` in a record (header included)."""
    pos = 24
    while pos < len(record):
        kind, size = record[pos : pos + 4], struct.unpack_from("<H", record, pos + 4)[0]
        if kind == name:
            return record[pos + 6 : pos + 6 + size]
        pos += 6 + size
    raise AssertionError(f"no {name!r} field")


def _record_at(data: bytes, kind: bytes) -> bytes:
    """The first record `kind` in a plugin, found by walking its groups."""
    pos = 0
    while pos < len(data):
        if data[pos : pos + 4] == b"GRUP":
            pos += 24
            continue
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if data[pos : pos + 4] == kind:
            return data[pos : pos + 24 + size]
        pos += 24 + size
    raise AssertionError(f"no {kind!r} record")


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)

    def write_toml(self, text: str, name: str = "plugin.toml") -> pathlib.Path:
        path = self.tmp / name
        path.write_text(textwrap.dedent(text))
        return path

    def run_esp(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )


class TestLoad(TempDir):
    def load(self, text: str):
        return esp.load(self.write_toml(text))

    def assert_rejects(self, text: str, key: str) -> None:
        with self.assertRaises(esp.EspError) as caught:
            self.load(text)
        self.assertTrue(str(caught.exception).startswith(f"{key}:"), caught.exception)

    def test_reads_the_sample(self):
        plugin = self.load(SAMPLE)
        self.assertEqual(plugin.masters, ("Skyrim.esm", "Update.esm"))
        self.assertFalse(plugin.esl)
        (quest,) = plugin.quests
        self.assertEqual((quest.editor_id, quest.object_id), ("SampleQuest", 0x800))
        self.assertTrue(quest.start_game_enabled and quest.run_once)
        self.assertEqual(
            quest.scripts[0].properties,
            (
                esp.Property("Reward", esp.FormRef(0x0F, "Skyrim.esm")),
                esp.Property("TestCell", esp.FormRef(0x801)),
            ),
        )
        self.assertEqual(quest.scripts[1], esp.Script("SecondScript"))
        self.assertEqual(plugin.cells, (esp.Cell("SampleCell", 0x801),))

    def test_defaults(self):
        plugin = self.load('masters = []\n[[quests]]\neditor_id = "Q"\nobject_id = 0x800\n')
        self.assertEqual(plugin, esp.Plugin((), False, (esp.Quest("Q", 0x800),), ()))

    def test_missing_key(self):
        self.assert_rejects('masters = []\n[[cells]]\neditor_id = "C"\n', "cells[0].object_id")

    def test_unknown_key(self):
        self.assert_rejects('masters = []\nesl = false\nauthor = "me"\n', "author")

    def test_unknown_nested_key(self):
        text = SAMPLE.replace('name = "Reward"', 'name = "Reward"\ntype = 1')
        self.assert_rejects(text, "quests[0].scripts[0].properties[0].type")

    def test_wrong_types(self):
        self.assert_rejects('masters = "Skyrim.esm"\n', "masters")
        self.assert_rejects("masters = [1]\n", "masters[0]")
        self.assert_rejects("masters = []\nesl = 1\n", "esl")
        # true is not an object ID, although Python's bool is an int.
        self.assert_rejects('masters = []\n[[cells]]\neditor_id = "C"\nobject_id = true\n', "cells[0].object_id")
        self.assert_rejects(SAMPLE.replace("form = { object_id = 0x801 }", "form = 0x801"),
                            "quests[0].scripts[0].properties[1].form")  # fmt: skip

    def test_invalid_toml(self):
        with self.assertRaisesRegex(esp.EspError, "not valid TOML"):
            self.load("masters = [\n")

    def test_master_must_be_listed(self):
        text = SAMPLE.replace('master = "Skyrim.esm"', 'master = "Dawnguard.esm"')
        self.assert_rejects(text, "quests[0].scripts[0].properties[0].form.master")

    def test_own_form_must_be_a_record_of_the_plugin(self):
        text = SAMPLE.replace("form = { object_id = 0x801 }", "form = { object_id = 0x900 }")
        self.assert_rejects(text, "quests[0].scripts[0].properties[1].form.object_id")

    def test_master_form_must_not_be_null(self):
        text = SAMPLE.replace("object_id = 0x00000F", "object_id = 0")
        self.assert_rejects(text, "quests[0].scripts[0].properties[0].form.object_id")

    def test_object_ids_start_at_0x800(self):
        self.assert_rejects(SAMPLE.replace("object_id = 0x800", "object_id = 0x7FF"), "quests[0].object_id")

    def test_esl_object_ids_end_at_0xfff(self):
        text = "esl = true\n" + SAMPLE.replace("object_id = 0x801", "object_id = 0x1000")
        self.assert_rejects(text, "cells[0].object_id")

    def test_plain_object_ids_may_pass_0xfff(self):
        text = SAMPLE.replace("object_id = 0x801", "object_id = 0x1000")
        self.assertEqual(self.load(text).cells[0].object_id, 0x1000)

    def test_object_ids_are_unique(self):
        self.assert_rejects(SAMPLE.replace("object_id = 0x801", "object_id = 0x800"), "cells[0].object_id")

    def test_editor_ids_are_unique_ignoring_case(self):
        self.assert_rejects(SAMPLE.replace('"SampleCell"', '"samplequest"'), "cells[0].editor_id")

    def test_editor_id_characters(self):
        self.assert_rejects(SAMPLE.replace('"SampleCell"', '"Sample Cell"'), "cells[0].editor_id")

    def test_script_and_property_names(self):
        self.assert_rejects(SAMPLE.replace('"SecondScript"', '"2ndScript"'), "quests[0].scripts[1].name")
        self.assert_rejects(SAMPLE.replace('"SecondScript"', '"samplequestscript"'), "quests[0].scripts[1].name")
        self.assert_rejects(SAMPLE.replace('"TestCell"', '"reward"'), "quests[0].scripts[0].properties[1].name")

    def test_masters(self):
        self.assert_rejects('masters = ["Skyrim.esm", "skyrim.ESM"]\n', "masters[1]")
        self.assert_rejects('masters = ["Skyrim.bsa"]\n', "masters[0]")
        self.assert_rejects('masters = ["../Skyrim.esm"]\n', "masters[0]")


class TestEncode(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "plugin.toml"
            path.write_text(SAMPLE)
            self.plugin = esp.load(path)
        self.data = esp.encode(self.plugin)

    def test_header(self):
        tes4 = self.data[: 24 + struct.unpack_from("<I", self.data, 4)[0]]
        # TES4, flags 0, form ID 0, timestamp and version control 0, form version 44.
        self.assertEqual(tes4[4 + 4 : 24], bytes.fromhex("00000000" "00000000" "00000000" "2c000000"))
        # Version 1.70; CELL, block, sub-block, the cell, QUST and the quest; next object ID.
        self.assertEqual(_field(tes4, b"HEDR"), struct.pack("<fII", 1.70, 6, 0x802))
        self.assertEqual(_field(tes4, b"MAST"), b"Skyrim.esm\0")
        self.assertEqual(_field(tes4, b"INTV"), struct.pack("<I", 1))

    def test_esl_flag(self):
        flags = struct.unpack_from("<I", esp.encode(esp.Plugin(("Skyrim.esm",), esl=True)), 8)[0]
        self.assertEqual(flags, 0x200)

    def test_quest(self):
        quest = _record_at(self.data, b"QUST")
        # Mod index 2: the plugin's own, after its two masters.
        self.assertEqual(struct.unpack_from("<I", quest, 12)[0], 0x02000800)
        self.assertEqual(_field(quest, b"EDID"), b"SampleQuest\0")
        # Start Game Enabled and Run Once; priority, unknown, quest type 0.
        self.assertEqual(_field(quest, b"DNAM"), bytes.fromhex("0101" "00" "00" "00000000" "00000000"))
        self.assertEqual(_field(quest, b"ANAM"), bytes(4))

    def test_vmad(self):
        # Worked out by hand from UESP's VMAD Field page; the property bytes
        # match SkyUI_SE.esp's PlayerRef property on SKI_FavoritesManagerInstance
        # (01 01 0000 ffff 14000000) apart from the form ID.
        expected = (
            "0500" "0200" "0200"  # version 5, object format 2, two scripts
            "1100" + b"SampleQuestScript".hex() + "00" "0200"  # status local, two properties
            "0600" + b"Reward".hex() + "01" "01"  # object, edited
            "0000" "ffff" "0f000000"  # unused, no alias, Skyrim.esm's 0x00000F
            "0800" + b"TestCell".hex() + "01" "01" "0000" "ffff" "01080002"  # this plugin's 0x801
            "0c00" + b"SecondScript".hex() + "00" "0000"  # no properties
            "02" "0000" "0000"  # fragments version 2, none, no fragment script
            "0000"  # no aliases
        )  # fmt: skip
        self.assertEqual(_field(_record_at(self.data, b"QUST"), b"VMAD").hex(), expected)

    def test_quest_without_scripts_has_no_vmad(self):
        data = esp.encode(esp.Plugin(("Skyrim.esm",), quests=(esp.Quest("Q", 0x800),)))
        with self.assertRaises(AssertionError):
            _field(_record_at(data, b"QUST"), b"VMAD")

    def test_cell_groups(self):
        # 0x801 is 2049: block 9, sub-block 4. Checked against a player home's
        # cell 0x09E893 (649363), which sits in block 3, sub-block 6.
        self.assertEqual(esp.cell_block(0x801), (9, 4))
        self.assertEqual(esp.cell_block(0x09E893), (3, 6))
        start = self.data.index(b"GRUP")
        cell = _record_at(self.data, b"CELL")
        self.assertEqual(
            self.data[start : start + 72].hex(),
            "47525550" + struct.pack("<I", 72 + len(cell)).hex() + b"CELL".hex() + "00000000" + "0" * 16
            + "47525550" + struct.pack("<I", 48 + len(cell)).hex() + "09000000" "02000000" + "0" * 16
            + "47525550" + struct.pack("<I", 24 + len(cell)).hex() + "04000000" "03000000" + "0" * 16,
        )  # fmt: skip
        self.assertEqual(_field(cell, b"EDID"), b"SampleCell\0")
        self.assertEqual(_field(cell, b"DATA"), b"\x01\x00")  # interior

    def test_cells_come_before_quests(self):
        # The order of the top groups in Skyrim.esm.
        self.assertLess(self.data.index(b"CELL"), self.data.index(b"QUST"))

    def test_record_order_does_not_depend_on_input_order(self):
        quests = (esp.Quest("B", 0x900), esp.Quest("A", 0x800))
        a = esp.encode(esp.Plugin(("Skyrim.esm",), quests=quests))
        b = esp.encode(esp.Plugin(("Skyrim.esm",), quests=quests[::-1]))
        self.assertEqual(a, b)


class TestParse(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "plugin.toml"
            path.write_text(SAMPLE)
            self.plugin = esp.load(path)
        self.data = esp.encode(self.plugin)

    def test_round_trip(self):
        self.assertEqual(esp.parse(self.data), self.plugin)

    def test_round_trip_sorts_records(self):
        plugin = esp.Plugin(
            ("Skyrim.esm",),
            esl=True,
            quests=(esp.Quest("B", 0x900), esp.Quest("A", 0x800, run_once=True)),
            cells=(esp.Cell("C2", 0x815), esp.Cell("C1", 0x80A), esp.Cell("C3", 0x81F)),
        )
        self.assertEqual(esp.parse(esp.encode(plugin)), esp.canonical(plugin))

    def test_empty_plugin(self):
        plugin = esp.Plugin(())
        self.assertEqual(esp.parse(esp.encode(plugin)), plugin)

    def test_every_truncation_fails(self):
        for size in range(len(self.data)):
            with self.subTest(size=size), self.assertRaises(esp.EspError):
                esp.parse(self.data[:size])

    def test_every_changed_byte_fails_or_round_trips(self):
        # A change dump would hide is one where parse succeeds but the bytes
        # differ from what build writes for the result.
        for offset in range(len(self.data)):
            changed = bytearray(self.data)
            changed[offset] ^= 0x01
            with self.subTest(offset=offset):
                try:
                    plugin = esp.parse(bytes(changed))
                except esp.EspError:
                    continue
                self.assertEqual(esp.encode(plugin), bytes(changed))

    def test_trailing_data_fails(self):
        with self.assertRaisesRegex(esp.EspError, "offset"):
            esp.parse(self.data + b"\0")

    def test_names_the_offset_of_a_layout_difference(self):
        changed = bytearray(self.data)
        offset = self.data.index(b"INTV") + 6
        changed[offset] = 2
        with self.assertRaisesRegex(esp.EspError, f"^offset {offset:#x}: not in the layout"):
            esp.parse(bytes(changed))

    def test_rejects_other_property_types(self):
        changed = bytearray(self.data)
        changed[self.data.index(b"Reward") + 6] = 2  # a string property
        with self.assertRaisesRegex(esp.EspError, "type 2, only objects"):
            esp.parse(bytes(changed))

    def test_rejects_compressed_records(self):
        quest = self.data.index(b"QUST", self.data.index(b"QUST") + 4)  # the record, not the group
        changed = bytearray(self.data)
        changed[quest + 10] |= 0x04  # flag 0x40000, compressed
        with self.assertRaises(esp.EspError):
            esp.parse(bytes(changed))


class TestAliasLoad(TempDir):
    def load(self, text: str):
        return esp.load(self.write_toml(text))

    def assert_rejects(self, text: str, key: str) -> None:
        with self.assertRaises(esp.EspError) as caught:
            self.load(text)
        self.assertTrue(str(caught.exception).startswith(f"{key}:"), caught.exception)

    def test_reads_the_alias(self):
        (quest,) = self.load(MCM).quests
        self.assertEqual(
            quest.aliases,
            (esp.Alias("PlayerAlias", esp.FormRef(0x14, "Skyrim.esm"), (esp.Script("SKI_PlayerLoadGameAlias"),)),),
        )

    def test_alias_scripts_are_optional(self):
        text = MCM.split("[[quests.aliases.scripts]]")[0]
        self.assertEqual(self.load(text).quests[0].aliases[0].scripts, ())

    def test_forced_ref_is_required(self):
        text = MCM.replace('forced_ref = { master = "Skyrim.esm", object_id = 0x14 }\n', "")
        self.assert_rejects(text, "quests[0].aliases[0].forced_ref")

    def test_forced_ref_is_in_a_master(self):
        text = MCM.replace('master = "Skyrim.esm", object_id = 0x14', "object_id = 0x800")
        self.assert_rejects(text, "quests[0].aliases[0].forced_ref.master")
        text = MCM.replace('master = "Skyrim.esm", object_id = 0x14', 'master = "Update.esm", object_id = 0x14')
        self.assert_rejects(text, "quests[0].aliases[0].forced_ref.master")

    def test_unknown_alias_key(self):
        self.assert_rejects(MCM.replace('name = "PlayerAlias"', 'name = "PlayerAlias"\nflags = 1'),
                            "quests[0].aliases[0].flags")  # fmt: skip

    def test_alias_names(self):
        self.assert_rejects(MCM.replace('"PlayerAlias"', '"Player Alias"'), "quests[0].aliases[0].name")
        second = '\n[[quests.aliases]]\nname = "playeralias"\nforced_ref = { master = "Skyrim.esm", object_id = 7 }\n'
        self.assert_rejects(MCM + second, "quests[0].aliases[1].name")

    def test_alias_scripts_follow_the_quest_script_rules(self):
        twice = MCM + '\n[[quests.aliases.scripts]]\nname = "ski_playerloadgamealias"\n'
        self.assert_rejects(twice, "quests[0].aliases[0].scripts[1].name")
        prop = '\n[[quests.aliases.scripts.properties]]\nname = "Quest"\nform = { object_id = 0x900 }\n'
        self.assert_rejects(MCM + prop, "quests[0].aliases[0].scripts[0].properties[0].form.object_id")


class TestAliasEncode(unittest.TestCase):
    def setUp(self) -> None:
        self.data = esp.encode(_load_text(MCM))
        self.quest = _record_at(self.data, b"QUST")

    def test_vmad(self):
        # SkyUI_SE.esp's SKI_MainInstance gives the same bytes apart from the
        # quest script's name and the quest's form ID.
        expected = (
            "0500" "0200" "0100"  # version 5, object format 2, one script
            "1000" + b"BeyondMasteryMCM".hex() + "00" "0000"  # local, no properties
            "02" "0000" "0000"  # fragments version 2, none, no fragment script
            "0100"  # one alias with scripts
            "0000" "0000" "00080001"  # unused, alias 0, quest 0x01000800
            "0500" "0200" "0100"  # version 5, object format 2, one script
            "1700" + b"SKI_PlayerLoadGameAlias".hex() + "00" "0000"
        )  # fmt: skip
        self.assertEqual(_field(self.quest, b"VMAD").hex(), expected)

    def test_alias_fields(self):
        # As SKI_MainInstance, apart from its FULL field.
        self.assertEqual(
            _field_names(self.quest),
            [b"EDID", b"VMAD", b"DNAM", b"NEXT", b"ANAM", b"ALST", b"ALID", b"FNAM", b"ALFR", b"VTCK", b"ALED"],
        )
        self.assertEqual(_field(self.quest, b"ANAM"), struct.pack("<I", 1))  # the next alias ID
        self.assertEqual(_field(self.quest, b"ALST"), struct.pack("<I", 0))
        self.assertEqual(_field(self.quest, b"ALID"), b"PlayerAlias\0")
        self.assertEqual(_field(self.quest, b"FNAM"), bytes(4))
        self.assertEqual(_field(self.quest, b"ALFR"), struct.pack("<I", 0x14))  # Skyrim.esm's PlayerRef
        self.assertEqual(_field(self.quest, b"VTCK"), bytes(4))
        self.assertEqual(_field(self.quest, b"ALED"), b"")

    def test_whole_plugin(self):
        # The bytes beyond-mastery's own generator wrote for its BeyondMastery.esp.
        vmad = _field(self.quest, b"VMAD")
        expected = (
            "54455334" "3b000000" "00020000" "00000000" "00000000" "2c000000"  # TES4, ESL flag
            "48454452" "0c00" "9a99d93f" "02000000" "01080000"  # 1.70, two records, next 0x801
            "4d415354" "0b00" + b"Skyrim.esm".hex() + "00" "44415441" "0800" "0000000000000000"
            "494e5456" "0400" "01000000"
            "47525550" + struct.pack("<I", 24 + len(self.quest)).hex() + b"QUST".hex() + "00000000" + "0" * 16
            + "51555354" + struct.pack("<I", len(self.quest) - 24).hex() + "00000000" "00080001" "00000000" "2c000000"
            "45444944" "1100" + b"BeyondMasteryMCM".hex() + "00"
            "564d4144" + struct.pack("<H", len(vmad)).hex() + vmad.hex()
            + "444e414d" "0c00" "0101" "00" "00" "00000000" "00000000"
            "4e455854" "0000"
            "414e414d" "0400" "01000000"
            "414c5354" "0400" "00000000"
            "414c4944" "0c00" + b"PlayerAlias".hex() + "00"
            "464e414d" "0400" "00000000"
            "414c4652" "0400" "14000000"
            "5654434b" "0400" "00000000"
            "414c4544" "0000"
        )  # fmt: skip
        self.assertEqual(self.data.hex(), expected)

    def test_alias_without_scripts_is_not_in_vmad(self):
        quest = esp.Quest(
            "Q",
            0x800,
            aliases=(
                esp.Alias("Plain", esp.FormRef(0x14, "Skyrim.esm")),
                esp.Alias("Scripted", esp.FormRef(0x7, "Skyrim.esm"), (esp.Script("S"),)),
            ),
        )
        record = _record_at(esp.encode(esp.Plugin(("Skyrim.esm",), quests=(quest,))), b"QUST")
        expected = (
            "0500" "0200" "0000"  # no quest scripts
            "02" "0000" "0000"  # no fragments
            "0100"  # one alias with scripts: alias 1 of quest 0x01000800
            "0000" "0100" "00080001"
            "0500" "0200" "0100" "0100" + b"S".hex() + "00" "0000"
        )  # fmt: skip
        self.assertEqual(_field(record, b"VMAD").hex(), expected)
        self.assertEqual(_field(record, b"ANAM"), struct.pack("<I", 2))
        self.assertEqual(_field_names(record).count(b"ALST"), 2)

    def test_aliases_without_scripts_need_no_vmad(self):
        quest = esp.Quest("Q", 0x800, aliases=(esp.Alias("Plain", esp.FormRef(0x14, "Skyrim.esm")),))
        record = _record_at(esp.encode(esp.Plugin(("Skyrim.esm",), quests=(quest,))), b"QUST")
        self.assertNotIn(b"VMAD", _field_names(record))


class TestAliasParse(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_text(MCM)
        self.data = esp.encode(self.plugin)

    def test_round_trip(self):
        self.assertEqual(esp.parse(self.data), self.plugin)

    def test_round_trip_of_aliases_with_and_without_scripts(self):
        prop = esp.Property("Owner", esp.FormRef(0x800))
        quest = esp.Quest(
            "Q",
            0x800,
            aliases=(
                esp.Alias("A", esp.FormRef(0x14, "Skyrim.esm")),
                esp.Alias("B", esp.FormRef(0x7, "Skyrim.esm"), (esp.Script("S", (prop,)), esp.Script("T"))),
                esp.Alias("C", esp.FormRef(0x14, "Skyrim.esm")),
            ),
        )
        plugin = esp.Plugin(("Skyrim.esm",), quests=(quest,))
        self.assertEqual(esp.parse(esp.encode(plugin)), plugin)

    def test_every_truncation_fails(self):
        for size in range(len(self.data)):
            with self.subTest(size=size), self.assertRaises(esp.EspError):
                esp.parse(self.data[:size])

    def test_every_changed_byte_fails_or_round_trips(self):
        for offset in range(len(self.data)):
            changed = bytearray(self.data)
            changed[offset] ^= 0x01
            with self.subTest(offset=offset):
                try:
                    plugin = esp.parse(bytes(changed))
                except esp.EspError:
                    continue
                self.assertEqual(esp.encode(plugin), bytes(changed))

    def test_rejects_scripts_for_a_missing_alias(self):
        changed = bytearray(self.data)
        alias_id = self.data.index(bytes.fromhex("0000" "0000" "00080001"), self.data.index(b"VMAD")) + 2
        changed[alias_id] = 3
        with self.assertRaisesRegex(esp.EspError, "scripts for alias 3, which the quest lacks"):
            esp.parse(bytes(changed))

    def test_rejects_a_forced_ref_to_this_plugin(self):
        changed = bytearray(self.data)
        changed[self.data.index(b"ALFR") + 6 + 3] = 1  # mod index 1: the plugin itself
        with self.assertRaisesRegex(esp.EspError, "forced_ref.master: missing"):
            esp.parse(bytes(changed))


class TestCommandLine(TempDir):
    def test_build_is_reproducible(self):
        source = self.write_toml(SAMPLE)
        for name in ("a.esp", "b.esp"):
            result = self.run_esp("build", str(source), str(self.tmp / name))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.tmp / "a.esp").read_bytes(), (self.tmp / "b.esp").read_bytes())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["a.esp", "b.esp", "plugin.toml"])

    def test_dump_prints_the_toml_shape(self):
        source = self.write_toml(SAMPLE)
        out = self.tmp / "out.esp"
        self.assertEqual(self.run_esp("build", str(source), str(out)).returncode, 0)
        result = self.run_esp("dump", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = tomllib.loads(SAMPLE)
        expected["esl"] = False
        expected["quests"][0]["scripts"][1]["properties"] = []
        expected["quests"][0]["aliases"] = []
        self.assertEqual(json.loads(result.stdout), expected)

    def test_dump_prints_aliases_in_the_toml_shape(self):
        source = self.write_toml(MCM)
        out = self.tmp / "out.esp"
        self.assertEqual(self.run_esp("build", str(source), str(out)).returncode, 0)
        result = self.run_esp("dump", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = tomllib.loads(MCM)
        expected["cells"] = []
        quest = expected["quests"][0]
        quest["scripts"][0]["properties"] = []
        quest["aliases"][0]["scripts"][0]["properties"] = []
        self.assertEqual(json.loads(result.stdout), expected)

    def test_build_reports_the_key_and_writes_nothing(self):
        source = self.write_toml(SAMPLE.replace("object_id = 0x800", "object_id = 0x10"))
        out = self.tmp / "out.esp"
        result = self.run_esp("build", str(source), str(out))
        self.assertEqual(result.returncode, 1)
        self.assertIn("quests[0].object_id", result.stderr)
        self.assertFalse(out.exists())

    def test_build_keeps_an_existing_file_on_error(self):
        source = self.write_toml("masters = 1\n")
        out = self.tmp / "out.esp"
        out.write_bytes(b"old")
        self.assertEqual(self.run_esp("build", str(source), str(out)).returncode, 1)
        self.assertEqual(out.read_bytes(), b"old")

    def test_build_into_a_missing_dir_fails(self):
        source = self.write_toml(SAMPLE)
        result = self.run_esp("build", str(source), str(self.tmp / "missing" / "out.esp"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("No such file", result.stderr)

    def test_dump_reports_the_offset(self):
        bad = self.tmp / "bad.esp"
        bad.write_bytes(b"TES4\x00")
        result = self.run_esp("dump", str(bad))
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"bad\.esp: offset 0x4: truncated")
        self.assertEqual(result.stdout, "")

    def test_missing_file(self):
        result = self.run_esp("dump", str(self.tmp / "missing.esp"))
        self.assertEqual(result.returncode, 1)

    def test_bad_usage(self):
        for args in ([], ["build", "only-one"], ["dump"], ["patch", "a", "b"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_esp(*args).returncode, 2)


if __name__ == "__main__":
    unittest.main()
