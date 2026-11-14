"""Tests for scripts/normalize_pex.py, which zeroes the times and empties the
user and machine names the Papyrus compiler writes into each .pex, so the
committed scripts are reproducible and carry no personal details."""

import importlib.util
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

from tests.support import TOOLCHAIN_ROOT

SCRIPT = TOOLCHAIN_ROOT / "scripts" / "normalize_pex.py"


def _load():
    spec = importlib.util.spec_from_file_location("normalize_pex", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pex = _load()


def _w(text: bytes) -> bytes:
    return struct.pack(">H", len(text)) + text


def synthetic(debug: bool = True) -> bytes:
    """A .pex header as the compiler writes it, followed by stand-in bytes."""
    out = struct.pack(">IBBHQ", 0xFA57C0DE, 3, 2, 1, 0x6AE344CC)
    out += _w(b"Test.psc") + _w(b"steamuser") + _w(b"PC-NAME")
    out += struct.pack(">H", 2) + _w(b"Test") + _w(b"GetState")
    if debug:
        return out + struct.pack(">BQ", 1, 0x6AE30000) + b"debug-and-objects"
    return out + b"\x00" + b"objects"


class TestNormalize(unittest.TestCase):
    def test_zeroes_times_and_names(self):
        header = pex.read_header(pex.normalize(synthetic()))
        self.assertEqual((header.compile_time, header.modification_time), (0, 0))
        self.assertEqual((header.user, header.machine), (b"", b""))

    def test_keeps_everything_else(self):
        out = pex.normalize(synthetic())
        self.assertEqual(out[:8], bytes.fromhex("fa57c0de" "03" "02" "0001"))
        self.assertEqual(pex.read_header(out).source, b"Test.psc")
        tail = _w(b"Test") + _w(b"GetState") + struct.pack(">BQ", 1, 0) + b"debug-and-objects"
        self.assertTrue(out.endswith(tail))

    def test_without_debug_info(self):
        out = pex.normalize(synthetic(debug=False))
        self.assertTrue(out.endswith(_w(b"GetState") + b"\x00objects"))
        self.assertIsNone(pex.read_header(out).modification_time)

    def test_is_idempotent(self):
        once = pex.normalize(synthetic())
        self.assertEqual(pex.normalize(once), once)

    def test_rejects_other_files(self):
        with self.assertRaisesRegex(pex.PexError, "not a .pex"):
            pex.normalize(b"TES4" + bytes(40))

    def test_rejects_truncated_files(self):
        data = synthetic()
        end_of_header = len(data) - len(b"debug-and-objects")
        for size in range(end_of_header):
            with self.subTest(size=size), self.assertRaises(pex.PexError):
                pex.normalize(data[:size])


class TestCommandLine(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)

    def run_script(self, *paths: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *map(str, paths)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_rewrites_in_place(self):
        a, b = self.tmp / "A.pex", self.tmp / "B.pex"
        a.write_bytes(synthetic())
        b.write_bytes(synthetic(debug=False))
        result = self.run_script(a, b)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(a.read_bytes(), pex.normalize(synthetic()))
        self.assertEqual(b.read_bytes(), pex.normalize(synthetic(debug=False)))

    def test_changes_no_file_if_one_is_not_a_pex(self):
        good, bad = self.tmp / "Good.pex", self.tmp / "Bad.pex"
        good.write_bytes(synthetic())
        bad.write_bytes(b"not a script")
        result = self.run_script(good, bad)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bad.pex: not a .pex", result.stderr)
        self.assertEqual(good.read_bytes(), synthetic())

    def test_missing_file(self):
        self.assertEqual(self.run_script(self.tmp / "Missing.pex").returncode, 1)

    def test_no_files_is_bad_usage(self):
        self.assertEqual(self.run_script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
