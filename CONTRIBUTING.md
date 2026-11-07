# Contributing

How changes get into skyrim-mod-toolchain. What to build is in the PRD ([docs/PRD.md](docs/PRD.md)).

## Workflow

Every change goes through a branch and a pull request; nothing is committed to `main` directly.

1. **Branch** from `main`, named `<area>/<slug>`, e.g. `setup/xwin-resume`.
2. **Commit** one logical change per commit, so the pull request can be reviewed commit by commit. Fix a commit that is not merged yet by folding the fix into it (`git commit --fixup=<commit>`, then `git rebase --autosquash main`) instead of adding a fix-up commit.
3. **Check** locally before opening the pull request: `scripts/test.sh` (the unit tests and shellcheck, see [README.md#tests](README.md#tests)) and `gitlint --commits main..HEAD`.
4. **Open a pull request** against `main`. It is merged with a merge commit, so the branch's commits land on `main` as they are.

## Commit messages

Titles follow [Conventional Commits](https://www.conventionalcommits.org): `<type>(<scope>): <description>`, with the area as the scope where a change belongs to one, e.g. `feat(setup): ...`. The types and line lengths are in [.gitlint](.gitlint).

[gitlint](https://jorisroovers.com/gitlint/) checks every commit message. Install it (`pipx install gitlint`, or your distribution's package) and run `gitlint install-hook` once per clone, so a bad message is rejected when you commit.

## CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every pull request and every push to `main`, and must pass before a merge. It runs the checks of step 3 of the workflow:

- gitlint on the pull request's commits;
- the tests (`scripts/test.sh`): the unit tests and shellcheck on every shell script.

## Editor settings

[.editorconfig](.editorconfig) sets indentation, line endings and final newlines for every file type in the repository. Most editors read it natively; VS Code needs the EditorConfig extension.
