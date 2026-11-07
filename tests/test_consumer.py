"""Tests for the CMake presets and helper, using tests/consumer as a project."""

import json
import os
import pathlib
import re
import subprocess
import unittest

from tests.support import TOOLCHAIN_ROOT

CONSUMER = TOOLCHAIN_ROOT / "tests" / "consumer"
CHECK_RUNTIME_DEPS = TOOLCHAIN_ROOT / "scripts" / "check_runtime_deps.py"
PLUGIN_NAME = "ToolchainConsumer"


ALLOWED_MACROS = {"sourceDir", "presetName"}


def _strings(node: object):
    """Yield every string value nested in parsed JSON."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


class PresetTests(unittest.TestCase):
    def test_presets_resolve(self) -> None:
        result = subprocess.run(
            ["cmake", "--list-presets"],
            cwd=CONSUMER,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"release"', result.stdout)

    def test_only_source_relative_macros(self) -> None:
        # ${fileDir} expands against the consumer's presets file, not this one.
        presets = json.loads((TOOLCHAIN_ROOT / "cmake" / "CMakePresets.json").read_text())
        for value in _strings(presets):
            for macro in re.findall(r"\$\{([^}]*)\}", value):
                with self.subTest(value=value):
                    self.assertIn(macro, ALLOWED_MACROS)

    def test_preset_paths_exist(self) -> None:
        presets = json.loads((TOOLCHAIN_ROOT / "cmake" / "CMakePresets.json").read_text())
        prefix = "${sourceDir}/"
        paths = []
        for preset in presets["configurePresets"]:
            for value in preset.get("cacheVariables", {}).values():
                if isinstance(value, str) and value.startswith(prefix):
                    paths.append(value.removeprefix(prefix))
        self.assertTrue(paths, "no ${sourceDir} paths found")
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((CONSUMER / path).exists())


class CrossBuildTests(unittest.TestCase):
    def test_cross_build(self) -> None:
        vcpkg_root = os.environ.get("VCPKG_ROOT")
        sysroot = pathlib.Path(os.environ.get("XWIN_SYSROOT", pathlib.Path.home() / "xwin-out"))
        if not (vcpkg_root and pathlib.Path(vcpkg_root).is_dir() and sysroot.is_dir()):
            if os.environ.get("REQUIRE_CROSS_BUILD") == "1":
                self.fail("REQUIRE_CROSS_BUILD=1 but VCPKG_ROOT or the xwin sysroot is missing")
            self.skipTest("VCPKG_ROOT or the xwin sysroot is missing")

        env = {**os.environ, "XWIN_SYSROOT": str(sysroot)}
        for command in (["--preset", "release"], ["--build", "--preset", "release"]):
            result = subprocess.run(
                ["cmake", *command], cwd=CONSUMER, env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])

        dlls = list((CONSUMER / "build").rglob(f"{PLUGIN_NAME}.dll"))
        self.assertEqual(len(dlls), 1, dlls)
        check = subprocess.run(
            [str(CHECK_RUNTIME_DEPS), str(dlls[0])], capture_output=True, text=True
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
