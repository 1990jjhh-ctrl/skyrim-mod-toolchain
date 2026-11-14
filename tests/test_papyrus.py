"""Tests for scripts/papyrus.sh, the Papyrus compile. The real compiler needs
the Creation Kit under Wine, which CI lacks, so a fake Wine stands in: it
records how it was called and writes a .pex per source, or fails as asked."""

import shlex
import struct

from tests.support import ScriptCase

SCRIPT = "scripts/papyrus.sh"


def _w(text: bytes) -> bytes:
    return struct.pack(">H", len(text)) + text


# A .pex as the compiler writes it, with a compile time and personal names.
RAW_PEX = (
    struct.pack(">IBBHQ", 0xFA57C0DE, 3, 2, 1, 0x6AE344CC)
    + _w(b"Script.psc") + _w(b"steamuser") + _w(b"PC-NAME")
    + struct.pack(">H", 0) + struct.pack(">BQ", 1, 0x6AE30000) + b"objects"
)  # fmt: skip


# Wine as papyrus.sh calls it: wine <compiler> Z:<source> -all -f=... -i=... -o=Z:<out>.
# It writes a copy of RAW_PEX per source. FAKE_SUCCEEDED and FAKE_FAILED override the summary; FAKE_EXIT the exit status.
FAKE_WINE = """\
#!/usr/bin/env bash
{{
	printf 'WINEPREFIX=%s\\n' "$WINEPREFIX"
	printf 'WINEDEBUG=%s\\n' "$WINEDEBUG"
	for arg in "$@"; do printf 'ARG=%s\\n' "$arg"; done
}} > {log}
source="${{2#Z:}}"
out="${{!#}}"; out="${{out#-o=Z:}}"
count=0
for psc in "$source"/*.psc; do
	cp {raw_pex} "$out/$(basename "$psc" .psc).pex"
	count=$((count + 1))
done
echo "Batch compile of $count files finished. ${{FAKE_SUCCEEDED:-$count}} succeeded, ${{FAKE_FAILED:-0}} failed."
exit "${{FAKE_EXIT:-0}}"
"""


class PapyrusTest(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.expose("python3", "realpath", "tee")
        self.game = self.tmp / "Skyrim Special Edition"
        (self.game / "Papyrus Compiler").mkdir(parents=True)
        (self.game / "Papyrus Compiler" / "PapyrusCompiler.exe").write_text("")
        self.base = self.game / "Data" / "Source" / "Scripts"
        self.base.mkdir(parents=True)
        (self.base / "TESV_Papyrus_Flags.flg").write_text("")
        self.proton = self.tmp / "proton" / "files"
        (self.proton / "bin").mkdir(parents=True)
        self.wine_log = self.tmp / "wine.log"
        wine = self.proton / "bin" / "wine"
        raw_pex = self.tmp / "raw.pex"
        raw_pex.write_bytes(RAW_PEX)
        wine.write_text(FAKE_WINE.format(log=shlex.quote(str(self.wine_log)), raw_pex=shlex.quote(str(raw_pex))))
        wine.chmod(0o755)
        self.source = self.tmp / "src"
        self.source.mkdir()
        for name in ("QuestScript", "OtherScript"):
            (self.source / f"{name}.psc").write_text(f"Scriptname {name}\n")
        self.out = self.tmp / "project" / "Scripts"
        self.env = {"SKYRIM_DIR": str(self.game), "PROTON_DIR": str(self.proton)}

    def papyrus(self, *args: str, **env: str):
        return self.run_script(SCRIPT, *args, env={**self.env, **env})

    def compile(self, *imports: str, **env: str):
        return self.papyrus(str(self.source), str(self.out), *imports, **env)

    def wine_call(self) -> tuple[dict[str, str], list[str]]:
        env, args = {}, []
        for line in self.wine_log.read_text().splitlines():
            key, _, value = line.partition("=")
            if key == "ARG":
                args.append(value)
            else:
                env[key] = value
        return env, args

    def test_compiles_and_normalizes(self):
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["OtherScript.pex", "QuestScript.pex"])
        data = (self.out / "QuestScript.pex").read_bytes()
        self.assertNotIn(b"steamuser", data)
        self.assertNotIn(b"PC-NAME", data)
        self.assertEqual(data[8:16], bytes(8))  # the compile time

    def test_calls_the_compiler_with_the_imports_in_order(self):
        extra = self.tmp / "headers"
        extra.mkdir()
        self.assertEqual(self.compile(str(extra)).returncode, 0)
        _, args = self.wine_call()
        compiler = self.game / "Papyrus Compiler" / "PapyrusCompiler.exe"
        self.assertEqual(args[:4], [str(compiler), f"Z:{self.source}", "-all", "-f=TESV_Papyrus_Flags.flg"])
        self.assertEqual(args[4], f"-i=Z:{self.source};Z:{extra};Z:{self.base}")
        self.assertRegex(args[5], r"^-o=Z:/")

    def test_keeps_the_prefix_and_log_in_the_cache(self):
        self.assertEqual(self.compile().returncode, 0)
        env, _ = self.wine_call()
        cache = self.home / ".cache" / "skyrim-mod-toolchain" / "papyrus"
        self.assertEqual(env, {"WINEPREFIX": str(cache / "wineprefix"), "WINEDEBUG": "-all"})
        self.assertIn("2 succeeded, 0 failed", (cache / "compile.log").read_text())
        # The raw output dir is removed; only the prefix and the log stay.
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ["compile.log", "wineprefix"])

    def test_honours_xdg_cache_home(self):
        cache = self.tmp / "xdg"
        self.assertEqual(self.compile(XDG_CACHE_HOME=str(cache)).returncode, 0)
        env, _ = self.wine_call()
        self.assertEqual(env["WINEPREFIX"], str(cache / "skyrim-mod-toolchain" / "papyrus" / "wineprefix"))

    def test_writes_nothing_into_the_game_folder(self):
        before = sorted(self.game.rglob("*"))
        self.assertEqual(self.compile().returncode, 0)
        self.assertEqual(sorted(self.game.rglob("*")), before)

    def test_a_failed_script_fails_and_writes_no_pex(self):
        result = self.compile(FAKE_SUCCEEDED="1", FAKE_FAILED="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not all 2 scripts compiled", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_summary_for_fewer_scripts_fails(self):
        result = self.compile(FAKE_SUCCEEDED="1", FAKE_FAILED="0")
        self.assertEqual(result.returncode, 1)

    def test_a_wine_error_fails(self):
        result = self.compile(FAKE_EXIT="3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("exited with an error", result.stderr)
        self.assertFalse(self.out.exists())

    def test_refuses_an_output_dir_in_the_game_folder(self):
        result = self.papyrus(str(self.source), str(self.game / "Data" / "Scripts"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("inside the game folder", result.stderr)
        self.assertFalse(self.wine_log.exists())

    def test_refuses_a_cache_in_the_game_folder(self):
        result = self.compile(XDG_CACHE_HOME=str(self.game / "cache"))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.game / "cache").exists())

    def test_missing_settings(self):
        for name in ("SKYRIM_DIR", "PROTON_DIR"):
            with self.subTest(name=name):
                result = self.run_script(SCRIPT, str(self.source), str(self.out),
                                         env={k: v for k, v in self.env.items() if k != name})  # fmt: skip
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{name} not set", result.stderr)

    def test_missing_compiler(self):
        (self.game / "Papyrus Compiler" / "PapyrusCompiler.exe").unlink()
        result = self.compile()
        self.assertEqual(result.returncode, 1)
        self.assertIn("PapyrusCompiler.exe", result.stderr)

    def test_no_sources(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        result = self.papyrus(str(empty), str(self.out))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no .psc files", result.stderr)

    def test_missing_import_dir(self):
        result = self.compile(str(self.tmp / "missing"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a directory", result.stderr)

    def test_semicolon_in_a_path(self):
        odd = self.tmp / "a;b"
        odd.mkdir()
        result = self.compile(str(odd))
        self.assertEqual(result.returncode, 1)
        self.assertIn("semicolon", result.stderr)

    def test_bad_usage(self):
        self.assertEqual(self.papyrus().returncode, 2)
        self.assertEqual(self.papyrus(str(self.source)).returncode, 2)
