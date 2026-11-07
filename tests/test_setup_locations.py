import json
import subprocess

from tests.support import TOOLCHAIN_ROOT, ScriptCase

SETUP = "scripts/setup.sh"
KEYS = [
    "llvm_mingw",
    "llvm_mingw_bin",
    "xwin_bin",
    "sysroot",
    "xwin_cache",
    "vcpkg_root",
    "vcpkg_archives",
    "tools_key",
    "sysroot_key",
    "vcpkg_baseline",
]
BASELINE = "a" * 40


def parse(output: str) -> dict[str, str]:
    """Split key=value lines; the value is everything after the first '='."""
    return dict(line.split("=", 1) for line in output.splitlines())


class PrintLocationsTest(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.expose("sha256sum", "python3")
        self.versions = self.tmp / "versions.env"
        self.write_versions(llvm="1", xwin="2")
        self.config = self.tmp / "vcpkg-configuration.json"
        self.write_config(BASELINE)

    def write_versions(self, *, llvm: str, xwin: str) -> None:
        self.versions.write_text(
            f'LLVM_MINGW_VERSION="{llvm}"\nXWIN_VERSION="{xwin}"\n'
        )

    def write_config(self, baseline: str) -> None:
        self.config.write_text(
            json.dumps({"default-registry": {"baseline": baseline}})
        )

    def locations(self, *args: str, env: dict[str, str] | None = None):
        return self.run_script(
            SETUP,
            "--print-locations",
            *args,
            env={
                "TOOLCHAIN_VERSIONS_FILE": str(self.versions),
                "TOOLCHAIN_VCPKG_CONFIG": str(self.config),
                **(env or {}),
            },
        )

    def parsed(self, env: dict[str, str] | None = None) -> dict[str, str]:
        result = self.locations(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return parse(result.stdout)

    def test_prints_every_key_once(self) -> None:
        result = self.locations()
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = [line.split("=", 1)[0] for line in result.stdout.splitlines()]
        self.assertEqual(keys, KEYS)

    def test_default_values(self) -> None:
        values = self.parsed()
        home = str(self.home)
        self.assertEqual(values["llvm_mingw"], f"{home}/.local/opt/llvm-mingw")
        self.assertEqual(values["llvm_mingw_bin"], f"{home}/.local/opt/llvm-mingw/bin")
        self.assertEqual(values["xwin_bin"], f"{home}/.local/bin/xwin")
        self.assertEqual(values["sysroot"], f"{home}/xwin-out")
        self.assertEqual(values["xwin_cache"], f"{home}/xwin-cache")
        self.assertEqual(values["vcpkg_root"], f"{home}/.local/share/vcpkg")
        self.assertEqual(values["vcpkg_archives"], f"{home}/.cache/vcpkg/archives")
        self.assertRegex(values["tools_key"], r"^[0-9a-f]{64}$")
        self.assertRegex(values["sysroot_key"], r"^[0-9a-f]{64}$")
        self.assertEqual(values["vcpkg_baseline"], BASELINE)

    def test_archives_follow_cache_overrides(self) -> None:
        xdg = self.tmp / "xdg"
        self.assertEqual(
            self.parsed({"XDG_CACHE_HOME": str(xdg)})["vcpkg_archives"],
            f"{xdg}/vcpkg/archives",
        )
        archives = self.tmp / "archives"
        self.assertEqual(
            self.parsed(
                {"XDG_CACHE_HOME": str(xdg), "VCPKG_DEFAULT_BINARY_CACHE": str(archives)}
            )["vcpkg_archives"],
            str(archives),
        )

    def test_print_locations_runs_nothing(self) -> None:
        for name in ("curl", "git", "xwin", "sudo", "apt-get"):
            self.stub(name)
        result = self.locations()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_log.exists(), False)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_locations_follow_overrides(self) -> None:
        vcpkg = self.tmp / "my-vcpkg"
        sysroot = self.tmp / "my-sysroot"
        llvm = self.tmp / "custom-dir"
        llvm.mkdir()
        values = self.parsed(
            {
                "VCPKG_ROOT": str(vcpkg),
                "XWIN_SYSROOT": str(sysroot),
                "LLVM_MINGW_BIN": str(llvm),
            }
        )
        self.assertEqual(values["vcpkg_root"], str(vcpkg))
        self.assertEqual(values["sysroot"], str(sysroot))
        self.assertEqual(values["llvm_mingw_bin"], str(llvm))
        self.assertEqual(values["llvm_mingw"], f"{self.home}/.local/opt/llvm-mingw")

    def test_managed_llvm_mingw_bin_is_not_an_override(self) -> None:
        managed = self.home / ".local" / "opt" / "llvm-mingw"
        (managed / "bin").mkdir(parents=True)
        values = self.parsed({"LLVM_MINGW_BIN": str(managed / "bin")})
        self.assertEqual(values["llvm_mingw"], str(managed))
        self.assertEqual(values["llvm_mingw_bin"], str(managed / "bin"))

    def test_locations_with_space_in_home(self) -> None:
        home = self.tmp / "my home"
        home.mkdir()
        self.home = home
        values = self.parsed({"HOME": str(home)})
        for key in ("llvm_mingw", "llvm_mingw_bin", "xwin_bin", "sysroot", "xwin_cache", "vcpkg_root"):
            self.assertTrue(values[key].startswith(f"{home}/"), (key, values[key]))
        self.assertEqual(values["vcpkg_archives"], f"{home}/.cache/vcpkg/archives")

    def test_keys_change_with_pins(self) -> None:
        before = self.parsed()
        self.write_versions(llvm="9", xwin="2")
        llvm_bumped = self.parsed()
        self.assertNotEqual(llvm_bumped["tools_key"], before["tools_key"])
        self.assertEqual(llvm_bumped["sysroot_key"], before["sysroot_key"])
        self.write_versions(llvm="1", xwin="9")
        xwin_bumped = self.parsed()
        self.assertNotEqual(xwin_bumped["tools_key"], before["tools_key"])
        self.assertNotEqual(xwin_bumped["sysroot_key"], before["sysroot_key"])
        self.write_config("b" * 40)
        self.assertEqual(self.parsed()["vcpkg_baseline"], "b" * 40)

    def test_keys_are_stable(self) -> None:
        self.assertEqual(self.parsed(), self.parsed())

    def test_exclusive_with_other_options(self) -> None:
        for option in ("--install", "--native-tests", "--check"):
            for args in (("--print-locations", option), (option, "--print-locations")):
                with self.subTest(args=args):
                    result = self.run_script(SETUP, *args)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")

    def assert_fails_without_output(self, **kwargs) -> None:
        result = self.locations(**kwargs)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_malformed_vcpkg_config_fails(self) -> None:
        self.config.write_text(json.dumps({"other": 1}))
        self.assert_fails_without_output()
        self.config.write_text(json.dumps({"default-registry": {"baseline": "main"}}))
        self.assert_fails_without_output()

    def test_missing_vcpkg_config_fails(self) -> None:
        self.config.unlink()
        self.assert_fails_without_output()

    def test_missing_sha256sum_fails(self) -> None:
        (self.sys / "sha256sum").unlink()
        self.assert_fails_without_output()

    def test_print_env_matches_locations(self) -> None:
        vcpkg = self.tmp / "my-vcpkg"
        sysroot = self.tmp / "my-sysroot"
        llvm = self.tmp / "custom-dir"
        llvm.mkdir()
        env = {
            "TOOLCHAIN_VERSIONS_FILE": str(self.versions),
            "TOOLCHAIN_VCPKG_CONFIG": str(self.config),
            "VCPKG_ROOT": str(vcpkg),
            "XWIN_SYSROOT": str(sysroot),
            "LLVM_MINGW_BIN": str(llvm),
        }
        values = self.parsed(env)
        result = subprocess.run(
            ["bash", "-c", f'. "{TOOLCHAIN_ROOT / SETUP}" --source-only && print_env'],
            env={"PATH": f"{self.bin}:{self.sys}", "HOME": str(self.home), **env},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        exports = {}
        for line in result.stdout.splitlines():
            if line.strip().startswith("export "):
                name, _, value = line.strip()[len("export "):].partition("=")
                exports[name] = value.strip('"')
        self.assertEqual(exports["VCPKG_ROOT"], values["vcpkg_root"])
        self.assertEqual(exports["XWIN_SYSROOT"], values["sysroot"])
        self.assertEqual(exports["LLVM_MINGW_BIN"], str(llvm))
        self.assertEqual(values["llvm_mingw_bin"], str(llvm))
        self.assertEqual(exports["PATH"], "$HOME/.local/bin:$PATH")
        self.assertEqual(values["xwin_bin"], f"{self.home}/.local/bin/xwin")
