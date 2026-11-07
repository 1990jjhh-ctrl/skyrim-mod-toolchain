import json
import shlex
import unittest

from tests.support import TOOLCHAIN_ROOT, ScriptCase

FORMAT = "scripts/format.sh"
LINT = "scripts/lint.sh"


STUB_BODY = """\
if [ "${1:-}" = --version ]; then echo "LLVM (http://llvm.org/):"; echo "  LLVM version 23.1.0"; exit 0; fi
if [ "${STUB_FAIL:-}" = 1 ]; then exit 1; fi"""


class FormatLintCase(ScriptCase):
    def setUp(self) -> None:
        super().setUp()
        self.expose("python3")
        self.project = self.tmp / "project"
        self.project.mkdir()

    def touch(self, *names: str) -> None:
        for name in names:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def tool_calls(self, name: str) -> list[list[str]]:
        """The calls that name made on files, without its --version probes."""
        return [args for args in self.calls(name) if args != ["--version"]]


class FormatTest(FormatLintCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub("clang-format", STUB_BODY)
        self.env = {"CLANG_FORMAT": str(self.bin / "clang-format")}

    def format(self, *args: str, env: dict[str, str] | None = None):
        return self.run_script(FORMAT, *args, env={**self.env, **(env or {})}, cwd=self.project)

    def test_check_passes_dry_run_and_sorted_files(self) -> None:
        self.touch("src/b.cpp", "src/a.h", "src/x.txt")
        result = self.format("--check", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.tool_calls("clang-format"),
            [["--dry-run", "--Werror", "src/a.h", "src/b.cpp"]],
        )
        self.assertIn("2 files clean", result.stdout)

    def test_fix_mode_uses_in_place(self) -> None:
        self.touch("src/a.cpp")
        result = self.format("src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tool_calls("clang-format"), [["-i", "src/a.cpp"]])

    def test_violations_exit_1(self) -> None:
        self.touch("src/a.cpp")
        result = self.format("--check", "src", env={"STUB_FAIL": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("violations", result.stderr)

    def test_no_paths_is_usage_error(self) -> None:
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.format(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage", result.stderr)

    def test_missing_tool_exit_2(self) -> None:
        self.touch("src/a.cpp")
        (self.bin / "clang-format").unlink()
        result = self.run_script(
            FORMAT,
            "src",
            env={"LLVM_MINGW_BIN": str(self.tmp / "nowhere")},
            cwd=self.project,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("clang-format not found", result.stderr)

    def test_no_files_does_not_call_clang_format(self) -> None:
        self.touch("src/x.txt")
        result = self.format("src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 files", result.stdout)
        self.assertEqual(self.calls("clang-format"), [])

    def test_paths_with_space(self) -> None:
        self.touch("my src/a.cpp")
        result = self.format("--check", "my src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.tool_calls("clang-format"),
            [["--dry-run", "--Werror", "my src/a.cpp"]],
        )

    def test_nonexistent_path_exit_2(self) -> None:
        self.touch("src/a.cpp")
        for args in (("nope",), ("--check", "src", "nope")):
            with self.subTest(args=args):
                result = self.format(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("no such path: nope", result.stderr)
                self.assertNotIn("files", result.stdout)
        self.assertEqual(self.calls("clang-format"), [])

    def test_symlinked_directories_are_not_followed(self) -> None:
        self.touch("src/a.cpp", "real/b.cpp")
        (self.project / "src" / "link").symlink_to(self.project / "real")
        (self.project / "src" / "loop").symlink_to(self.project)
        result = self.format("--check", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.tool_calls("clang-format"),
            [["--dry-run", "--Werror", "src/a.cpp"]],
        )


class LintTest(FormatLintCase):
    def setUp(self) -> None:
        super().setUp()
        # Keeps the compile database clang-tidy was given; lint.sh removes it.
        self.seen_db = self.tmp / "seen_compile_commands.json"
        self.stub(
            "clang-tidy",
            f"{STUB_BODY}\n"
            f'cp "$2/compile_commands.json" {shlex.quote(str(self.seen_db))}',
        )
        self.env = {"CLANG_TIDY": str(self.bin / "clang-tidy")}

    def write_db(self, *files: str, command: str = "cc") -> None:
        build = self.project / "build"
        build.mkdir()
        for f in files:
            (self.project / f).parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"directory": str(self.project), "command": command, "file": str(self.project / f)}
            for f in files
        ]
        (build / "compile_commands.json").write_text(json.dumps(entries))

    def tidy_files(self) -> list[list[str]]:
        """The files of each clang-tidy run; checks it got -p <dir> --quiet first."""
        runs = []
        for args in self.tool_calls("clang-tidy"):
            self.assertEqual(args[0], "-p")
            self.assertEqual(args[2], "--quiet")
            runs.append(args[3:])
        return runs

    def lint(self, *args: str, env: dict[str, str] | None = None):
        return self.run_script(LINT, *args, env={**self.env, **(env or {})}, cwd=self.project)

    def test_lint_filters_compile_db(self) -> None:
        self.write_db("src/a.cpp", "lib/x.cpp")
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tidy_files(), [[str(self.project / "src" / "a.cpp")]])

    def test_lint_findings_exit_1(self) -> None:
        self.write_db("src/a.cpp")
        result = self.lint("build", "src", env={"STUB_FAIL": "1"})
        self.assertEqual(result.returncode, 1)

    def test_lint_without_compile_db_exit_2(self) -> None:
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 2)
        self.assertIn("compile_commands.json", result.stderr)

    def test_lint_malformed_compile_db_exit_2(self) -> None:
        self.write_db("src/a.cpp")
        (self.project / "build" / "compile_commands.json").write_text("{not json")
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read", result.stderr)
        self.assertNotIn("0 files", result.stdout)
        self.assertEqual(self.calls("clang-tidy"), [])

    def test_lint_nonexistent_path_exit_2(self) -> None:
        self.write_db("src/a.cpp")
        result = self.lint("build", "nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no such path: nope", result.stderr)
        self.assertNotIn("0 files", result.stdout)

    def test_lint_usage_error(self) -> None:
        self.write_db("src/a.cpp")
        self.assertEqual(self.lint("build").returncode, 2)
        self.assertEqual(self.lint().returncode, 2)

    def test_lint_missing_tool_exit_2(self) -> None:
        self.write_db("src/a.cpp")
        (self.bin / "clang-tidy").unlink()
        result = self.run_script(
            LINT,
            "build",
            "src",
            env={"LLVM_MINGW_BIN": str(self.tmp / "nowhere")},
            cwd=self.project,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("clang-tidy not found", result.stderr)

    def test_lint_no_matching_files_does_not_call_clang_tidy(self) -> None:
        self.write_db("lib/x.cpp")
        (self.project / "src").mkdir()
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 files", result.stdout)
        self.assertEqual(self.calls("clang-tidy"), [])

    def test_lint_paths_with_space(self) -> None:
        self.write_db("my src/a.cpp")
        result = self.lint("build", "my src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tidy_files(), [[str(self.project / "my src" / "a.cpp")]])

    def test_lint_drops_pch_use_from_compile_db(self) -> None:
        # The PCH is built by another clang than clang-tidy's and cannot be read.
        pch = "/Yu/b/cmake_pch.hxx /Fp/b/cmake_pch.cxx.pch /FI/b/cmake_pch.hxx"
        self.write_db("src/a.cpp", command=f"clang-cl /W4 {pch} /c src/a.cpp")
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        (entry,) = json.loads(self.seen_db.read_text())
        self.assertEqual(
            entry["arguments"],
            ["clang-cl", "/W4", "/FI/b/cmake_pch.hxx", "/c", "src/a.cpp"],
        )
        self.assertEqual(entry["file"], str(self.project / "src" / "a.cpp"))

    def test_lint_drops_pch_use_from_arguments_form(self) -> None:
        self.write_db("src/a.cpp")
        db = self.project / "build" / "compile_commands.json"
        (entry,) = json.loads(db.read_text())
        del entry["command"]
        entry["arguments"] = ["clang-cl", "/Yupch.hxx", "/Fppch.pch", "/FIpch.hxx", "a.cpp"]
        db.write_text(json.dumps([entry]))
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        (seen,) = json.loads(self.seen_db.read_text())
        self.assertEqual(seen["arguments"], ["clang-cl", "/FIpch.hxx", "a.cpp"])

    def test_lint_leaves_the_build_compile_db_alone(self) -> None:
        self.write_db("src/a.cpp", command="clang-cl /Yupch.hxx a.cpp")
        db = self.project / "build" / "compile_commands.json"
        before = db.read_text()
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(db.read_text(), before)
        (args,) = self.tool_calls("clang-tidy")
        self.assertFalse((self.project / args[1]).exists(), "temporary database left behind")

    def test_lint_file_argument(self) -> None:
        self.write_db("src/a.cpp", "src/b.cpp")
        self.touch("src/a.cpp", "src/b.cpp")
        result = self.lint("build", "src/a.cpp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tidy_files(), [[str(self.project / "src" / "a.cpp")]])

    def test_lint_path_prefix_is_not_a_match(self) -> None:
        self.write_db("src/a.cpp", "src2/b.cpp")
        result = self.lint("build", "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tidy_files(), [[str(self.project / "src" / "a.cpp")]])


class ConfigTest(unittest.TestCase):
    def test_tidy_config_is_project_neutral(self) -> None:
        text = (TOOLCHAIN_ROOT / ".clang-tidy").read_text()
        self.assertIn("HeaderFilterRegex: '.*'", text)
        self.assertIn("ExcludeHeaderFilterRegex: '/(lib|build|vcpkg_installed)/'", text)
        self.assertNotIn("/modules/", text)


if __name__ == "__main__":
    unittest.main()
