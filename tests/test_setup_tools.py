import hashlib
import io
import pathlib
import subprocess
import tarfile
import unittest

from tests.support import TOOLCHAIN_ROOT, ScriptCase

SETUP = "scripts/setup.sh"
COMPILER = "x86_64-w64-mingw32-clang++"
XWIN_VERSION = "0.10.0"


def make_tarball(path: pathlib.Path, members: dict[str, str]) -> str:
    """Write a tarball of executable stub files; return its sha256."""
    mode = "w:xz" if path.name.endswith(".xz") else "w:gz"
    with tarfile.open(path, mode) as archive:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SetupToolsTest(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.expose("tar", "xz", "gzip", "sha256sum")
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        self.llvm_tarball = self.fixtures / "llvm.tar.xz"
        llvm_sha = make_tarball(
            self.llvm_tarball, {f"llvm-mingw-x/bin/{COMPILER}": "#!/bin/sh\n"}
        )
        self.xwin_tarball = self.fixtures / "xwin.tar.gz"
        xwin_sha = make_tarball(
            self.xwin_tarball,
            {f"xwin-{XWIN_VERSION}-x86_64-unknown-linux-musl/xwin": "#!/bin/sh\n"},
        )
        self.set_versions(llvm_sha, xwin_sha)
        # Serves both fixtures by the name of the requested file.
        self.stub(
            "curl",
            'while (($#)); do case "$1" in -o) out="$2"; shift ;; *) url="$1" ;; esac; shift; done\n'
            f'case "$url" in *.tar.xz) src={self.llvm_tarball} ;; *) src={self.xwin_tarball} ;; esac\n'
            'cp "$src" "$out"',
        )

    def set_versions(self, llvm_sha: str, xwin_sha: str) -> None:
        self.versions = self.tmp / "versions.env"
        self.versions.write_text(
            'LLVM_MINGW_VERSION="x"\n'
            f'LLVM_MINGW_SHA256="{llvm_sha}"\n'
            f'XWIN_VERSION="{XWIN_VERSION}"\n'
            f'XWIN_SHA256="{xwin_sha}"\n'
        )

    def step(self, name: str, *, env=None):
        """Run one setup step in isolation through --source-only."""
        environment = {
            "PATH": f"{self.bin}:{self.sys}",
            "HOME": str(self.home),
            "TOOLCHAIN_VERSIONS_FILE": str(self.versions),
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
    def opt(self) -> pathlib.Path:
        return self.home / ".local" / "opt"

    @property
    def stamp(self) -> pathlib.Path:
        return self.opt / "llvm-mingw" / ".toolchain-version"

    def install_compiler(self, version: str | None = "x") -> pathlib.Path:
        """Fake an llvm-mingw install, stamped with `version` (None: no stamp)."""
        path = self.opt / "llvm-mingw" / "bin" / COMPILER
        path.parent.mkdir(parents=True)
        path.touch(mode=0o755)
        if version is not None:
            self.stamp.write_text(f"{version}\n")
        return path

    def test_downloads_and_installs_llvm_mingw(self):
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.opt / "llvm-mingw" / "bin" / COMPILER).exists())
        self.assertEqual([p.name for p in self.opt.iterdir()], ["llvm-mingw"])
        self.assertEqual(self.stamp.read_text(), "x\n")

    def test_home_with_space(self):
        self.home = self.tmp / "my home"
        self.home.mkdir()
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.opt / "llvm-mingw" / "bin" / COMPILER).exists())
        result = self.step("install_xwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".local" / "bin" / "xwin").exists())

    def test_existing_llvm_mingw_not_downloaded(self):
        self.install_compiler()
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("curl"), [])

    def test_llvm_mingw_of_another_version_is_reinstalled(self):
        self.install_compiler(version="old")
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 1)
        self.assertEqual(self.stamp.read_text(), "x\n")

    def test_unstamped_llvm_mingw_is_reinstalled(self):
        self.install_compiler(version=None)
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 1)
        self.assertEqual(self.stamp.read_text(), "x\n")

    def test_llvm_mingw_bin_at_managed_dir_is_reinstalled(self):
        compiler = self.install_compiler(version="old")
        result = self.step("install_llvm_mingw", env={"LLVM_MINGW_BIN": str(compiler.parent)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 1)
        self.assertEqual(self.stamp.read_text(), "x\n")

    def test_valid_llvm_mingw_bin_used(self):
        bin_dir = self.tmp / "mingw" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / COMPILER).touch(mode=0o755)
        result = self.step("install_llvm_mingw", env={"LLVM_MINGW_BIN": str(bin_dir)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("curl"), [])

    def test_invalid_llvm_mingw_bin_rejected(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        result = self.step("install_llvm_mingw", env={"LLVM_MINGW_BIN": str(empty)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("LLVM_MINGW_BIN", result.stderr)
        self.assertEqual(self.calls("curl"), [])

    def test_partial_llvm_mingw_is_reinstalled(self):
        partial = self.opt / "llvm-mingw"
        (partial / "lib").mkdir(parents=True)
        (partial / "lib" / "leftover").touch()
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 1)
        self.assertTrue((partial / "bin" / COMPILER).exists())
        self.assertFalse((partial / "lib" / "leftover").exists())

    def test_checksum_mismatch_aborts_cleanly(self):
        self.set_versions("0" * 64, "0" * 64)
        result = self.step("install_llvm_mingw")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sha256", result.stderr)
        self.assertFalse((self.opt / "llvm-mingw").exists())
        self.assertEqual(list(self.opt.iterdir()), [])

    def test_xwin_checksum_mismatch_aborts_cleanly(self):
        self.set_versions("0" * 64, "0" * 64)
        result = self.step("install_xwin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sha256", result.stderr)
        self.assertEqual(list((self.home / ".local" / "bin").iterdir()), [])

    def test_xwin_installed_at_pinned_version(self):
        result = self.step("install_xwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".local" / "bin" / "xwin").is_file())
        self.assertEqual(len(self.calls("curl")), 1)

    def test_pinned_xwin_on_path_kept(self):
        self.stub("xwin", f'echo "xwin {XWIN_VERSION}"')
        result = self.step("install_xwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("curl"), [])

    def test_other_xwin_version_replaced(self):
        self.stub("xwin", 'echo "xwin 0.9.0"')
        result = self.step("install_xwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("curl")), 1)
        self.assertTrue((self.home / ".local" / "bin" / "xwin").exists())


if __name__ == "__main__":
    unittest.main()
