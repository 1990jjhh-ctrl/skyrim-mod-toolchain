#!/usr/bin/env bash
# Runs the unit tests, then shellcheck on every tracked shell script.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0

python3 -m unittest discover -s tests -t . || status=1

# The shellcheck binary is required: a missing one must fail, not skip.
if ! command -v shellcheck >/dev/null; then
	echo "test.sh: shellcheck is not installed" >&2
	exit 1
fi
git ls-files -z '*.sh' | xargs -0 --no-run-if-empty shellcheck || status=1

exit "$status"
