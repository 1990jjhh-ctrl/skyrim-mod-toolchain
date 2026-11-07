"""Shared helpers for testing the toolchain's scripts against stubbed commands."""

import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest

TOOLCHAIN_ROOT = pathlib.Path(__file__).resolve().parent.parent

BASE_TOOLS = (
    "bash", "env", "cat", "dirname", "basename", "mkdir", "rm", "mv", "cp", "ln",
    "ls", "sed", "grep", "head", "sort", "find", "mktemp", "chmod", "tr",
)  # fmt: skip

ARG_SEPARATOR = "\x1f"


def read_versions(path: pathlib.Path = TOOLCHAIN_ROOT / "versions.env") -> dict[str, str]:
    """Parse the NAME="value" lines of a versions file, ignoring comments."""
    versions = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        (versions[name],) = shlex.split(value)
    return versions


class ScriptCase(unittest.TestCase):
    """Runs a script with a private HOME and a PATH holding only stubs and BASE_TOOLS."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        self.sys = self.tmp / "sys"
        for directory in (self.home, self.bin, self.sys):
            directory.mkdir()
        self.calls_log = self.tmp / "calls.log"
        self.expose(*BASE_TOOLS)

    def stub(self, name: str, body: str = "exit 0") -> pathlib.Path:
        """Create an executable `name` that logs its arguments, then runs `body`."""
        path = self.bin / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            f"log={shlex.quote(str(self.calls_log))}\n"
            f"printf '%s' {shlex.quote(name)} >> \"$log\"\n"
            "for arg in \"$@\"; do printf '\\x1f%s' \"$arg\" >> \"$log\"; done\n"
            "printf '\\n' >> \"$log\"\n"
            f"{body}\n"
        )
        path.chmod(0o755)
        return path

    def calls(self, name: str) -> list[list[str]]:
        """The argument lists `name` was called with, in order."""
        if not self.calls_log.exists():
            return []
        calls = []
        for line in self.calls_log.read_text().splitlines():
            command, *args = line.split(ARG_SEPARATOR)
            if command == name:
                calls.append(args)
        return calls

    def expose(self, *names: str) -> None:
        """Make the real system commands `names` available to the script."""
        for name in names:
            real = shutil.which(name)
            if real is None:
                self.fail(f"{name} is not installed on this machine")
            link = self.sys / name
            if not link.exists():
                link.symlink_to(real)

    def run_script(
        self,
        script: str,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: pathlib.Path | None = None,
    ) -> subprocess.CompletedProcess:
        """Run `script` (relative to the repo root) in the isolated environment."""
        environment = {"PATH": f"{self.bin}:{self.sys}", "HOME": str(self.home)}
        environment.update(env or {})
        return subprocess.run(
            [str(TOOLCHAIN_ROOT / script), *args],
            env=environment,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
