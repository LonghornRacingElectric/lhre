# Agent instructions: simulation/

This tree is an exception to the root [AGENTS.md](../AGENTS.md). It is not
a Bazel package tree
([ADR-012](../docs/architecture/012-simulation-outside-bazel.md)).
`simulation` is in `.bazelignore`. Do not add `BUILD.bazel` files here.

## Before you start

1. Read [README.md](README.md) for the targets and the container.
2. Find the skill for your task in [docs/skills/](docs/README.md#skills).
   Read all of it before you do the task.

| Task | Skill |
| ---- | ----- |
| Answer a question with a new study | [add-study](docs/skills/add-study/SKILL.md) |
| Run a BobSim evaluation, envelope or sweep | [run-bobsim-target](docs/skills/run-bobsim-target/SKILL.md) |
| Change a vehicle parameter | [edit-vehicle](docs/skills/edit-vehicle/SKILL.md) |
| Move the BobSim pin | [bump-bobsim](docs/skills/bump-bobsim/SKILL.md) |

## Build, test, verify

- Run every command through `make` from `simulation/`. Every target runs in
  Docker. Do not run BobSim with a host Python.
- `make test` must pass before you finish.
- If Docker is not running, start it or tell the user. Do not say a change
  works if you did not run it.
- On Windows, run `make` from Git Bash. Do not pass absolute container paths
  such as `/lhre/...` to `docker` from Git Bash. Git Bash changes them to
  Windows paths. Use paths relative to `simulation/`, or set
  `MSYS_NO_PATHCONV=1`.

## Rules

- The vehicle is `vehicle/vehicle.yml` at the repo root. Do not make a
  second vehicle file. A study that needs a changed vehicle writes a copy
  under `out/<study>/`.
- Do not edit `bobsim/`. It is a submodule. Make BobSim changes in
  [BobDyn/BobSim](https://github.com/BobDyn/BobSim), then bump the pin.
- Do not copy BobSim code into this repo. BobSim is GPL-3.0 and lhre is MIT.
  Import BobSim instead.
- Do not edit Modelica `.mo` files with regular expressions. Change
  `vehicle.yml`.
- Do not commit `out/`. Commit the study `README.md`, `run.py` and at most
  two small figures.
- Keep each study in its own folder. Do not import one study from another.
  Move shared code into BobSim.
- Files are LF. On Windows, check new files before you commit.

## Comments

- Do not add a comment unless the code cannot work without the reader
  knowing it. This applies to every file: Python, Makefiles, compose files,
  CI workflows, `BUILD.bazel`, `.gitignore`, YAML and `mkdocs.yml`.
- Do not add header comments or docstrings that say what a file or function
  does. The name and the README say that.
- Put the reason for a decision in the README, the docs or the PR
  description, not in a comment.
- If a comment is necessary, keep it to one line.
- If you change code under an existing comment, keep the comment true. Do
  not delete other people's comments without a reason.

## Writing

Write docs, comments and study results in Simplified Technical English:

- Use short sentences in active voice. Use one idea per sentence.
- Use a verb, not a noun form of the action.
- Use one name for one thing.
- Keep real uncertainty. "May" stays "may".
- Give numbers with units. Say which vehicle and BobSim SHA gave them.
