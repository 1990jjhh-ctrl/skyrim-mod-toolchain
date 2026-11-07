import json
import subprocess
import unittest

from tests.support import TOOLCHAIN_ROOT, read_versions


class PinsTest(unittest.TestCase):
    def test_versions_has_exactly_the_known_names(self):
        self.assertEqual(
            set(read_versions()),
            {"LLVM_MINGW_VERSION", "LLVM_MINGW_SHA256", "XWIN_VERSION", "XWIN_SHA256"},
        )

    def test_checksums_are_sha256(self):
        versions = read_versions()
        for name in ("LLVM_MINGW_SHA256", "XWIN_SHA256"):
            self.assertRegex(versions[name], r"^[0-9a-f]{64}$", name)

    def test_versions_is_sourceable(self):
        result = subprocess.run(
            ["bash", "-c", 'set -u; . versions.env; echo "$LLVM_MINGW_VERSION $XWIN_VERSION"'],
            cwd=TOOLCHAIN_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        versions = read_versions()
        self.assertEqual(
            result.stdout.strip(), f"{versions['LLVM_MINGW_VERSION']} {versions['XWIN_VERSION']}"
        )

    def test_vcpkg_baseline_is_a_commit(self):
        config = json.loads((TOOLCHAIN_ROOT / "vcpkg-configuration.json").read_text())
        self.assertRegex(config["default-registry"]["baseline"], r"^[0-9a-f]{40}$")

    def test_commonlibsse_is_pinned(self):
        entry = subprocess.run(
            ["git", "-C", str(TOOLCHAIN_ROOT), "ls-files", "-s", "lib/commonlibsse-ng"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertTrue(entry, "lib/commonlibsse-ng is not in the index")
        mode, commit, *_ = entry.split()
        self.assertEqual(mode, "160000")
        self.assertRegex(commit, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
