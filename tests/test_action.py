"""Text checks on the composite setup action (it can only run on GitHub)."""

import re
import shutil
import subprocess
import unittest

from tests.support import TOOLCHAIN_ROOT, read_versions

ACTION = TOOLCHAIN_ROOT / ".github" / "actions" / "setup" / "action.yml"
SETUP_PATH = "$GITHUB_ACTION_PATH/../../../scripts/setup.sh"
EXPRESSION = re.compile(r"\$\{\{.*?\}\}")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def run_blocks(text: str) -> list[str]:
    """The dedented bodies of every `run: |` block, found by indentation."""
    lines = text.splitlines()
    blocks = []
    for number, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        base = indent_of(line)
        body = []
        for following in lines[number + 1 :]:
            if following.strip() and indent_of(following) <= base:
                break
            body.append(following)
        margin = min(indent_of(row) for row in body if row.strip())
        blocks.append("\n".join(row[margin:] for row in body))
    return blocks


def cache_paths(text: str) -> list[str]:
    """Every `path:` entry of every actions/cache@v6 step."""
    lines = text.splitlines()
    paths = []
    for number, line in enumerate(lines):
        if "uses: actions/cache@v6" not in line:
            continue
        dash_indent = indent_of(line)
        for index in range(number + 1, len(lines)):
            row = lines[index]
            if row.strip().startswith("- ") and indent_of(row) <= dash_indent:
                break
            key, _, value = row.strip().partition(":")
            if key != "path":
                continue
            if value.strip() == "|":
                for entry in lines[index + 1 :]:
                    if not entry.strip() or indent_of(entry) <= indent_of(row):
                        break
                    paths.append(entry.strip())
            else:
                paths.append(value.strip())
            break
    return paths


class ActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ACTION.read_text()

    def test_action_has_no_pins_or_home_paths(self) -> None:
        baseline = subprocess.run(
            ["grep", "-o", r"[0-9a-f]\{40\}", str(TOOLCHAIN_ROOT / "vcpkg-configuration.json")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for value in [*read_versions().values(), baseline]:
            # Whole tokens only, so a version like 19 does not match "2019".
            token = re.compile(rf"(?<![\w.]){re.escape(value)}(?![\w.])")
            with self.subTest(value=value):
                self.assertIsNone(token.search(self.text))
        for value in ("~/", "$HOME", "github.com/"):
            with self.subTest(value=value):
                self.assertNotIn(value, self.text)

    def test_action_calls_setup_through_action_path(self) -> None:
        self.assertNotIn("submodules", self.text)
        for flag in ("--print-locations", "--install"):
            with self.subTest(flag=flag):
                self.assertIn(f'"{SETUP_PATH}" {flag}', self.text)

    def test_action_uses_source_dir_for_vcpkg_json(self) -> None:
        uses = [row for row in self.text.splitlines() if "inputs.source-dir" in row]
        self.assertTrue(uses)
        for row in uses:
            with self.subTest(row=row):
                self.assertRegex(row, r"^\s+\w+: \$\{\{ inputs\.source-dir \}\}$")
        self.assertIn('"$GITHUB_WORKSPACE/$SOURCE_DIR/vcpkg.json"', self.text)

    def test_run_blocks_contain_no_expressions(self) -> None:
        for block in run_blocks(self.text):
            with self.subTest(block=block.splitlines()[0]):
                self.assertNotIn("${{", block)

    def test_vcpkg_cache_key_is_unique_per_run(self) -> None:
        prefix = "skse-vcpkg-${{ steps.locations.outputs.vcpkg_baseline }}-"
        full = prefix + "${{ steps.locations.outputs.vcpkg_key }}-"
        self.assertIn(f"key: {full}${{{{ github.run_id }}}}", self.text)
        self.assertRegex(self.text, rf"restore-keys: \|\n\s+{re.escape(full)}\n\s+{re.escape(prefix)}\n")

    def test_cache_steps_use_location_outputs(self) -> None:
        paths = cache_paths(self.text)
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path):
                self.assertRegex(path, r"^\$\{\{ steps\.\w+\.outputs\.\w+ \}\}$")

    def test_every_run_step_uses_bash(self) -> None:
        self.assertEqual(self.text.count("run: "), self.text.count("shell: bash"))

    def test_shell_blocks_pass_shellcheck(self) -> None:
        self.assertIsNotNone(shutil.which("shellcheck"), "shellcheck is not installed")
        blocks = run_blocks(self.text)
        self.assertGreaterEqual(len(blocks), 3)
        for block in blocks:
            script = "#!/usr/bin/env bash\n" + EXPRESSION.sub("placeholder", block) + "\n"
            with self.subTest(block=block.splitlines()[0]):
                result = subprocess.run(
                    ["shellcheck", "-s", "bash", "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
