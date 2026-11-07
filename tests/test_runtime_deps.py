"""Tests for scripts/check_runtime_deps.py, the gate that keeps a plugin off
the player's Visual C++ runtime."""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.pe_fixture import build_pe
from tests.support import TOOLCHAIN_ROOT

SCRIPT = TOOLCHAIN_ROOT / "scripts" / "check_runtime_deps.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_runtime_deps", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


crd = _load()


def truncated_names() -> dict[str, bytes]:
    """Images cut at the start of a DLL name and in the middle of one."""
    image = build_pe(["KERNEL32.dll", "MSVCP140.dll"])
    start = image.index(b"MSVCP140.dll")
    return {"at name start": image[:start], "mid-name": image[: start + len("MSVCP14")]}


class TestForbidden(unittest.TestCase):
    def test_flags_the_redistributable_runtime(self):
        self.assertEqual(
            crd.forbidden(["KERNEL32.dll", "MSVCP140.dll", "VCRUNTIME140.dll", "USER32.dll"]),
            ["MSVCP140.dll", "VCRUNTIME140.dll"],
        )

    def test_is_case_insensitive(self):
        # Import tables spell these either way; missing one spelling would let
        # the dynamic CRT back in unnoticed.
        self.assertEqual(crd.forbidden(["msvcp140.dll"]), ["msvcp140.dll"])
        self.assertEqual(crd.forbidden(["Vcruntime140_1.DLL"]), ["Vcruntime140_1.DLL"])

    def test_catches_the_numbered_variants(self):
        # The STL is split across several DLLs; any of them is the same problem.
        names = ["msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll"]
        self.assertEqual(crd.forbidden(names), names)

    def test_leaves_the_ucrt_and_os_alone(self):
        # The UCRT ships with the OS and is not version-skewed like the redist.
        self.assertEqual(
            crd.forbidden(["api-ms-win-crt-runtime-l1-1-0.dll", "KERNEL32.dll", "ole32.dll"]),
            [],
        )


class PeCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)

    def write(self, name: str, data: bytes) -> pathlib.Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path


class TestImportParsing(PeCase):
    def test_reads_the_import_table(self):
        path = self.write("a.dll", build_pe(["KERNEL32.dll", "MSVCP140.dll"]))
        self.assertEqual(crd.imported_dlls(path), ["KERNEL32.dll", "MSVCP140.dll"])

    def test_no_import_directory(self):
        self.assertEqual(crd.imported_dlls(self.write("a.dll", build_pe([]))), [])

    def test_rejects_non_pe(self):
        with self.assertRaises(ValueError):
            crd.imported_dlls(self.write("a.dll", b"not a dll"))

    def test_rejects_pe32(self):
        with self.assertRaisesRegex(ValueError, r"PE32\+"):
            crd.imported_dlls(self.write("a.dll", build_pe([], magic=0x10B)))

    def test_rejects_truncated_name_table(self):
        # A cut name ("MSVCP14") would otherwise pass as a harmless import.
        for case, data in truncated_names().items():
            with self.subTest(case), self.assertRaisesRegex(ValueError, "truncated"):
                crd.imported_dlls(self.write("a.dll", data))

    def test_rejects_truncated_images(self):
        image = build_pe(["KERNEL32.dll"])
        for data in (b"MZ", image[: len(image) // 2], image[:0x60]):
            with self.subTest(size=len(data)), self.assertRaisesRegex(ValueError, "truncated"):
                crd.imported_dlls(self.write("a.dll", data))


class TestCli(PeCase):
    def check(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )

    def test_cli_exit_codes(self):
        clean = self.write("clean.dll", build_pe(["KERNEL32.dll"]))
        dirty = self.write("dirty.dll", build_pe(["KERNEL32.dll", "MSVCP140.dll"]))

        self.assertEqual(self.check().returncode, 2)
        self.assertEqual(self.check(clean).returncode, 0)

        result = self.check(clean, dirty)
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        self.assertEqual(sum(line.startswith("OK   ") for line in lines), 1)
        self.assertEqual(sum(line.startswith("FAIL ") for line in lines), 1)

    def test_unreadable_inputs_are_errors_not_tracebacks(self):
        missing = self.tmp / "missing.dll"
        not_pe = self.write("not_pe.dll", b"not a dll")
        for path in (missing, not_pe):
            with self.subTest(path=path.name):
                result = self.check(path)
                self.assertEqual(result.returncode, 2)
                self.assertRegex(result.stdout, rf"(?m)^ERROR {path}:")
                self.assertNotIn("Traceback", result.stderr)

    def test_truncated_name_table_is_an_error(self):
        for case, data in truncated_names().items():
            with self.subTest(case):
                path = self.write("a.dll", data)
                result = self.check(path)
                self.assertEqual(result.returncode, 2)
                self.assertRegex(result.stdout, rf"(?m)^ERROR {path}: .*truncated")

    def test_error_does_not_hide_a_fail(self):
        dirty = self.write("dirty.dll", build_pe(["MSVCP140.dll"]))
        result = self.check(self.tmp / "missing.dll", dirty)
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stdout, rf"(?m)^FAIL {dirty} ")


if __name__ == "__main__":
    unittest.main()
