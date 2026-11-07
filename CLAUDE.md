# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

skyrim-mod-toolchain is the shared Linux cross-compile toolchain for skyrim-mcp, trials-of-mastery and beyond-mastery. Only the PRD and the project tooling exist so far.

Check the `Status:` line of [docs/PRD.md](docs/PRD.md) before implementing. While it reads Draft, work on the PRD, not on code.

## Where things are defined

- Goals, scope, requirements and milestones: [docs/PRD.md](docs/PRD.md)
- The PRD ends with its open questions ([docs/PRD.md#9-open-questions](docs/PRD.md#9-open-questions)). Don't settle one in code; raise it with the user.

## Workflow

Branches, commits, pull requests and what CI checks: [CONTRIBUTING.md](CONTRIBUTING.md). Before reporting a change as done, run the local checks in [CONTRIBUTING.md#workflow](CONTRIBUTING.md#workflow) (step 3) and report their results, including any you could not run.
