import pathlib
import time
import unittest

from tests.support import ScriptCase


class ScriptCaseTest(ScriptCase):
    def script(self, body: str) -> str:
        """Write a throwaway bash script into the temp dir and return its path."""
        path = self.tmp / "script.sh"
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)
        return str(path)

    def test_stub_logs_arguments_intact(self):
        self.stub("tool")
        result = self.run_script(self.script('tool "a b" "" c'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("tool"), [["a b", "", "c"]])

    def test_calls_are_per_command(self):
        self.stub("one")
        self.stub("two")
        self.stub("never")
        self.run_script(self.script("one x; two y; one z"))
        self.assertEqual(self.calls("one"), [["x"], ["z"]])
        self.assertEqual(self.calls("two"), [["y"]])
        self.assertEqual(self.calls("never"), [])

    def test_unexposed_command_is_absent(self):
        result = self.run_script(self.script("command -v git"))
        self.assertNotEqual(result.returncode, 0)

    def test_exposed_command_is_present(self):
        self.expose("git")
        result = self.run_script(self.script("command -v git"))
        self.assertEqual(result.returncode, 0)

    def test_stub_shadows_an_exposed_tool(self):
        self.stub("cat", "echo stubbed")
        result = self.run_script(self.script("cat"))
        self.assertEqual(result.stdout.strip(), "stubbed")

    def test_home_is_the_temp_home(self):
        result = self.run_script(self.script('echo "$HOME"'))
        self.assertEqual(pathlib.Path(result.stdout.strip()), self.home)

    def test_env_reaches_the_script(self):
        result = self.run_script(self.script('echo "$FOO"'), env={"FOO": "bar baz"})
        self.assertEqual(result.stdout.strip(), "bar baz")

    def test_script_cannot_wait_on_stdin(self):
        started = time.monotonic()
        result = self.run_script(self.script("read -r x"))
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 10)


if __name__ == "__main__":
    unittest.main()
