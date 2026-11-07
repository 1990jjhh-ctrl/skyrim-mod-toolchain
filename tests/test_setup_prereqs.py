import pathlib
import unittest

from tests.support import ScriptCase

SETUP = "scripts/setup.sh"
COMMANDS = (
    "git curl tar xz zip unzip pkg-config cmake ninja wine python3 sha256sum "
    "clang-cl lld-link llvm-rc llvm-mt"
).split()
LLVM_TOOLS = ("clang-cl", "lld-link", "llvm-rc", "llvm-mt")


class SetupPrereqsTest(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.llvm_prefix = self.tmp / "usr" / "bin"
        self.llvm_prefix.mkdir(parents=True)

    def run_script(self, script, *args, env=None, cwd=None):
        # Keep the host's real /usr/bin/clang-cl-* out of the test.
        environment = {"SETUP_LLVM_PREFIX": str(self.llvm_prefix), **(env or {})}
        return super().run_script(script, *args, env=environment, cwd=cwd)

    def stub_all(self, *, without: tuple[str, ...] = ()) -> None:
        for name in COMMANDS:
            if name not in without:
                self.stub(name)

    def missing_tokens(self, result) -> set[str]:
        (line,) = [l for l in result.stdout.splitlines() if l.startswith("Missing:")]
        return set(line.removeprefix("Missing:").split())

    def test_all_present_check_passes(self):
        self.stub_all()
        self.stub("sudo")
        result = self.run_script(SETUP, "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("sudo"), [])

    def test_reports_missing_and_hint(self):
        self.stub_all(without=("wine", "llvm-mt"))
        result = self.run_script(SETUP, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing:", result.stdout)
        self.assertEqual(self.missing_tokens(result), {"wine", "llvm-mt"})
        self.assertIn("apt-get install", result.stdout)

    def test_never_sudo_without_install(self):
        self.stub_all(without=("wine",))
        self.stub("sudo")
        self.run_script(SETUP, "--check")
        self.assertEqual(self.calls("sudo"), [])

    def test_install_updates_then_installs(self):
        self.stub_all(without=("wine",))
        self.stub("sudo", '"$@"')
        self.stub("apt-get", f"cp {self.bin / 'git'} {self.bin / 'wine'}")
        result = self.run_script(SETUP, "--install", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        sudo_calls = self.calls("sudo")
        self.assertEqual(len(sudo_calls), 2)
        self.assertEqual(sudo_calls[0], ["apt-get", "update"])
        self.assertEqual(
            sudo_calls[1][:4], ["apt-get", "install", "-y", "--no-install-recommends"]
        )
        self.assertIn("wine", sudo_calls[1])

    def test_install_without_apt(self):
        self.stub_all(without=("wine",))
        result = self.run_script(SETUP, "--install", "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--install supports apt only", result.stderr)

    def test_native_tests_needs_cxx(self):
        self.stub_all()
        self.assertEqual(self.run_script(SETUP, "--check").returncode, 0)
        result = self.run_script(SETUP, "--native-tests", "--check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.missing_tokens(result), {"c++"})
        self.assertIn("build-essential", result.stdout)

    def test_versioned_llvm_tools_accepted(self):
        self.stub_all(without=LLVM_TOOLS)
        for name in LLVM_TOOLS:
            (self.llvm_prefix / f"{name}-21").touch(mode=0o755)
        result = self.run_script(SETUP, "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_option(self):
        result = self.run_script(SETUP, "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage", result.stderr)

    def test_home_with_space(self):
        self.stub_all()
        home = self.tmp / "my home"
        home.mkdir()
        result = self.run_script(SETUP, "--check", env={"HOME": str(home)})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runs_from_any_cwd(self):
        self.stub_all()
        result = self.run_script(SETUP, "--check", cwd=pathlib.Path("/"))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
