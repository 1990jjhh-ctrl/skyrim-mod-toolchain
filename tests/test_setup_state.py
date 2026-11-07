import json
import pathlib
import subprocess
import unittest

from tests.support import TOOLCHAIN_ROOT, ScriptCase

SETUP = "scripts/setup.sh"
XWIN_VERSION = "0.10.0"


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class SetupStateTest(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.expose("git", "python3", "touch")
        self.stub(
            "xwin",
            f'if [[ "$1" == --version ]]; then echo "xwin {XWIN_VERSION}"; exit 0; fi\n'
            'while (($#)); do [[ "$1" == --output ]] && out="$2"; shift; done\n'
            'mkdir -p "$out/VC/Tools/MSVC" "$out/Windows Kits/10"',
        )
        self.versions = self.tmp / "versions.env"
        self.versions.write_text(f'XWIN_VERSION="{XWIN_VERSION}"\n')
        self.make_vcpkg_fixture()

    def make_vcpkg_fixture(self) -> None:
        self.upstream = self.tmp / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q")
        git(self.upstream, "config", "user.email", "t@example.com")
        git(self.upstream, "config", "user.name", "t")
        git(self.upstream, "config", "commit.gpgsign", "false")
        script = self.upstream / "bootstrap-vcpkg.sh"
        script.write_text(
            '#!/bin/sh\ntouch "$(dirname "$0")/vcpkg"\nchmod +x "$(dirname "$0")/vcpkg"\n'
        )
        script.chmod(0o755)
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-qm", "first")
        self.baseline = git(self.upstream, "rev-parse", "HEAD")
        (self.upstream / "extra").write_text("x")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-qm", "second")
        self.latest = git(self.upstream, "rev-parse", "HEAD")
        self.config = self.tmp / "vcpkg-configuration.json"
        self.config.write_text(
            json.dumps({"default-registry": {"baseline": self.baseline}})
        )

    def step(self, name: str, *, env=None):
        environment = {
            "PATH": f"{self.bin}:{self.sys}",
            "HOME": str(self.home),
            "TOOLCHAIN_VERSIONS_FILE": str(self.versions),
            "TOOLCHAIN_VCPKG_CONFIG": str(self.config),
            "VCPKG_GIT_URL": str(self.upstream),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            **(env or {}),
        }
        return subprocess.run(
            ["bash", "-c", f'. "{TOOLCHAIN_ROOT / SETUP}" --source-only && {name}'],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    @property
    def vcpkg(self) -> pathlib.Path:
        return self.home / ".local" / "share" / "vcpkg"

    def splats(self) -> list[list[str]]:
        return [c for c in self.calls("xwin") if "splat" in c]

    def test_splat_runs_once(self):
        first = self.step("install_sysroot")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Microsoft", first.stdout)
        second = self.step("install_sysroot")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.splats()), 1)
        self.assertIn("--use-winsysroot-style", self.splats()[0])

    @property
    def splat_stamp(self) -> pathlib.Path:
        return self.home / "xwin-out" / ".splat-complete"

    def test_unstamped_sysroot_is_splatted(self):
        for part in ("VC/Tools/MSVC", "Windows Kits/10"):
            (self.home / "xwin-out" / part).mkdir(parents=True)
        result = self.step("install_sysroot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.splats()), 1)
        self.assertTrue(self.splat_stamp.is_file())

    def test_stamped_sysroot_is_not_splatted(self):
        self.splat_stamp.parent.mkdir()
        self.splat_stamp.touch()
        result = self.step("install_sysroot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.splats(), [])

    def test_failed_splat_is_not_stamped(self):
        self.stub(
            "xwin",
            f'if [[ "$1" == --version ]]; then echo "xwin {XWIN_VERSION}"; exit 0; fi\n'
            'exit 1',
        )
        result = self.step("install_sysroot")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.splat_stamp.exists())

    def test_half_splat_is_redone(self):
        (self.home / "xwin-out" / "VC" / "Tools" / "MSVC").mkdir(parents=True)
        result = self.step("install_sysroot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.splats()), 1)

    def test_vcpkg_cloned_at_baseline(self):
        result = self.step("install_vcpkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.vcpkg, "rev-parse", "HEAD"), self.baseline)
        self.assertTrue((self.vcpkg / "vcpkg").exists())

    def test_partial_vcpkg_clone_resumes(self):
        self.vcpkg.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "-q", "--no-checkout", str(self.upstream), str(self.vcpkg)],
            check=True,
        )
        result = self.step("install_vcpkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.vcpkg, "rev-parse", "HEAD"), self.baseline)
        self.assertTrue((self.vcpkg / "vcpkg").exists())

    def pin_tip(self) -> None:
        self.config.write_text(
            json.dumps({"default-registry": {"baseline": self.latest}})
        )

    def test_fresh_clone_at_tip_baseline_is_populated(self):
        self.pin_tip()
        result = self.step("install_vcpkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.vcpkg, "rev-parse", "HEAD"), self.latest)
        self.assertTrue((self.vcpkg / "vcpkg").exists())

    def test_partial_clone_at_tip_baseline_is_populated(self):
        self.pin_tip()
        self.vcpkg.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "-q", "--no-checkout", str(self.upstream), str(self.vcpkg)],
            check=True,
        )
        result = self.step("install_vcpkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.vcpkg, "rev-parse", "HEAD"), self.latest)
        self.assertTrue((self.vcpkg / "vcpkg").exists())

    def test_vcpkg_root_at_managed_dir_is_moved_to_baseline(self):
        self.vcpkg.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(self.upstream), str(self.vcpkg)], check=True)
        (self.vcpkg / "vcpkg").touch(mode=0o755)
        result = self.step("install_vcpkg", env={"VCPKG_ROOT": str(self.vcpkg)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.vcpkg, "rev-parse", "HEAD"), self.baseline)

    def test_foreign_vcpkg_root_is_left_alone(self):
        foreign = self.tmp / "foreign vcpkg"
        subprocess.run(["git", "clone", "-q", str(self.upstream), str(foreign)], check=True)
        (foreign / "vcpkg").touch(mode=0o755)
        result = self.step("install_vcpkg", env={"VCPKG_ROOT": str(foreign)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(foreign, "rev-parse", "HEAD"), self.latest)
        self.assertIn(self.latest, result.stderr)
        self.assertIn(self.baseline, result.stderr)
        self.assertFalse(self.vcpkg.exists())

    def test_invalid_vcpkg_root_rejected(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        result = self.step("install_vcpkg", env={"VCPKG_ROOT": str(empty)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("VCPKG_ROOT", result.stderr)
        self.assertFalse(self.vcpkg.exists())

    def test_prints_env(self):
        result = self.step("print_env")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("VCPKG_ROOT", "XWIN_SYSROOT", "LLVM_MINGW_BIN"):
            self.assertIn(f"export {name}=", result.stdout)
        self.assertIn("$HOME/.local/bin", result.stdout)


if __name__ == "__main__":
    unittest.main()
