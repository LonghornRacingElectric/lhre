# Agent instructions

Instructions for AI coding agents working in this repo. Humans should read
[README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md) first. Agents
should too.

## Documentation is part of every change

Docs live **next to the code**. Every package directory with non-trivial
code has a `README.md`. CI publishes the whole tree as a site (MkDocs, see
`mkdocs.yml`). Repo-wide pages with no single home directory (for example
`build-system.md`) live in `docs/`. Treat docs as a required output of your
work, not an afterthought:

- **If you change behavior, update the adjacent `README.md` in the same
  change.** That includes new/renamed Bazel targets, new macro options,
  changed flags or defaults, new conventions, and changed workflows.
- **If you create a directory with code in it, create its `README.md`.**
  Say what it is, why it exists, how to use its targets, and any gotchas.
  Follow the existing voice. It is terse, explains *why*, and does not
  narrate code line by line. `drivers/lhal/README.md` and
  `tools/firmware/README.md` are the style reference.
- **If you find undocumented or wrongly documented code, fix the docs** as
  part of whatever you're doing there.
- **If a change makes a repo-wide structural decision** (layout, build
  system, new abstraction layer or convention), record it as an ADR in
  [docs/architecture/](docs/architecture/README.md). That README gives the
  format and says when to write one. ADRs are immutable. A changed decision
  gets a new ADR that supersedes the old one, not an edit.
- Verify docs before finishing: `uv run --group docs mkdocs build --strict`
  must pass. Broken links between `.md` files fail the build.
- Linking rules: between markdown files use relative links
  (`../tools/firmware/README.md`). To *code or config files* use full
  GitHub URLs (`https://github.com/LonghornRacingElectric/lhre/blob/main/...`)
  — relative links to non-markdown files 404 on the published site.
- Docs can also be embedded in source files. A comment block that opens
  with `/** md` (until `**/`) or `// md` (until `// end md`) becomes its
  own page. Prefer the adjacent README for anything structural. Use
  embedded blocks for docs that would go stale if separated from the code.

## Everything an agent needs is in this repo

A contributor with only GitHub access must be able to do any task here.
Slack, Notion and personal knowledge bases can add context. A rule, a
procedure or a skill that a task needs goes in this repo, next to the code
it is about. If you used context from outside the repo to make a decision,
write the decision and its reason in the PR or the adjacent README.

## Writing

Write docs, skills, comments, error messages and PR descriptions in
Simplified Technical English (STE). Load the
[simple-technical-english](docs/skills/simple-technical-english/SKILL.md)
skill before you write or rewrite them. The main rules:

- Use short sentences in active voice, with one idea in each sentence.
- Write an instruction as a command, in 20 words or fewer.
- Do not use semicolons or phrasal verbs.
- Use one name for one thing, and define each abbreviation.
- Keep real uncertainty. "May" stays "may".
- Give every number with its unit.

## Skills

A skill is a `SKILL.md` that tells an agent how to do one task here.

- Put a skill next to the code it is about, in a `docs/skills/<name>/`
  folder. A repo-wide skill goes in the root `docs/skills/`.
- Link a new skill from the `AGENTS.md` of its area, so agents find it.
- If a skill was wrong or missing a step when you used it, fix the skill in
  the same PR.

## Tests are the gate

Agent-written code is reviewed less closely than it's written. So CI has to
catch what a reviewer would have ([ADR-017](docs/architecture/017-tests-gate-merges.md)).
Follow [docs/testing.md](docs/testing.md). The rules that matter most:

- A behavior change ships with a test that fails without it. Cover the
  edges (limits, saturation, faults, units), not only the happy path.
- Aim for high coverage of the lines you add or change. CI reports it on
  every PR without a fixed percent. A low number needs a reason.
- For a big overhaul, land a tests-first PR that pins current behavior,
  then the refactor.
- Do not do any of these to get CI green:
  - Delete, skip or loosen a test.
  - Widen a size budget.
  - Mark a test flaky. `flaky = True`, `DISABLED_` and `GTEST_SKIP` fail CI.
  - Add a lint suppression.

  If a test is genuinely wrong, fix it in its own commit and say why in the
  PR.
- Tests are hermetic: no network, no wall clock, seeded randomness, no
  absolute or home paths, writes only under the test's temp dir.
- In `simulation/`, test lhre's own tools, vehicle files and study
  plumbing. BobSim and BobLib are tested in BobDyn. Do not test them again
  here.
- Fill in the PR template's "How it's tested" with the test that pins the
  change.

## Build, test, verify

```bash
bazel test //...                        # build + test everything (remote by default)
bazel test --config=local //...         # same, entirely on this machine
bazel run //tools/format                # clang-format everything (CI enforces :check)
uv run --group lint ruff check          # Python lint (CI enforces)
uv run --group lint actionlint          # lint the CI workflows
uv run --group docs mkdocs build --strict   # docs must build cleanly
bazel coverage //boards/... //drivers/...  # then diff-cover (tools/coverage/README.md)
```

## CI

All checks live in one workflow,
[`ci.yml`](https://github.com/LonghornRacingElectric/lhre/blob/main/.github/workflows/ci.yml).
It runs on every PR, on every push to `main`, weekly, and by hand.

- `Changes` lists the files a PR touches, renamed files' old paths
  included. [`tools/ci/changes.py`](tools/ci/README.md) maps them to
  areas. Each area's job runs only when its paths changed. The areas are
  Bazel on Linux and Windows, simulation fast checks, FourPost, autonomy
  (colcon), BEVO, docs, telemetry, the telemetry viewer and the HIL job
  dashboard.
  A change to the viewer's npm app alone runs only the viewer's job.
  `Format` always runs. To change which paths run what, edit `AREAS` in
  `changes.py` and add a case to its test.
- `Lint` always runs: ruff for Python and shellcheck for scripts outside
  `autonomy/`, and actionlint for the workflows (see
  [tools/lint/README.md](tools/lint/README.md)). Fix a new violation. A
  suppression (`# noqa: <code>`, `# shellcheck disable=<code>`) needs a
  reason after it.
- `All checks passed` is the only required check. It fails if any job failed or
  was cancelled, or if `Changes` failed. A job skips only when `Changes`
  says its paths are untouched. If `Changes` fails, every job runs.
- To read a failure, open the run's summary page. `All checks passed`
  lists every job's result. A failed Bazel job lists each failed test with
  the end of its log.
- To add a check:
  - Add a job to `ci.yml`.
  - If the job is path-gated, add an area for it in `changes.py` and an
    output in `Changes`.
  - Add the job to the `needs` of `all-checks`. `All checks passed` fails
    if a job is missing from its `needs`.
- The telemetry job builds the images, smoke-tests every one of them and
  runs the integration tests against Docker Compose. Without the repo
  secrets it fails. Fork PRs and Dependabot cannot get the secrets, so
  they skip the integration tests with a warning.
- GitHub runs no CI at all on a PR that conflicts with its base. Such a PR
  shows no checks rather than red ones.
  [`pr-conflicts.yml`](https://github.com/LonghornRacingElectric/lhre/blob/main/.github/workflows/pr-conflicts.yml)
  labels such PRs `merge-conflict`. It comments once, after every push to
  `main` and every six hours. Merge or rebase the base to get CI back.

Firmware for a board: `bazel build //boards/VCU:vcu`. Flashing targets
(`:openocd`, `:dfu`) and the VS Code debug launches need hardware attached.
Do not run them.

## Repo rules that bite

- `boards/*/Core/` is CubeMX-generated. Never hand-edit, format, or "fix"
  anything in it. Changes go through the board's `.ioc` and regeneration
  (see [CONTRIBUTING.md](CONTRIBUTING.md#regenerating-cubemx-code-safely)).
- Application code depends on `//drivers/lhal` interfaces only, never on
  ST HAL directly. That keeps it host-testable. Visibility enforces it: a
  direct dep on `//drivers/stm32/...` is a build error. The fix is an LHAL
  interface, not a visibility grant.
- New board = `bazel run //tools:new_board -- boards/<Name>/<Name>.ioc`
  (the `--` separates Bazel's options from the script's arguments), not a
  copy of VCU's `BUILD.bazel`. Board BUILD files state only what the
  `firmware_project` macro can't derive from `mcu` and the CubeMX layout.
- `new_board.py` generates the board entries in `.vscode/launch.json` and
  `.vscode/tasks.json`, and all of `tools/debug/svd_lock.bzl`. Do not edit
  them by hand. Regenerate them with
  `bazel run //tools:new_board -- <ioc> --vscode-only`.
  [tools/debug/README.md](tools/debug/README.md) documents the on-target
  debug architecture (hermetic gdb/OpenOCD/SVD staged under
  `bazel-bin/tools/debug/`).
- Windows builds are shell-free: nothing that builds on Windows may need
  bash.
  - No `genrule` without `cmd_bat`.
  - No `ctx.actions.run_shell`.
  - No generated wrapper scripts. Exec the tool (`ctx.actions.run`,
    `run_binary`, `hermetic_launcher` for `bazel run` wrappers), or mark the
    target non-Windows.

  `.bazelrc` pins the shell there to a path that doesn't exist. A violation
  fails the Windows CI job with `lhre-windows-builds-are-shell-free` in the
  error. The fix is never "install bash" (see
  [docs/build-system.md](docs/build-system.md#windows-builds-are-shell-free)).
- Every module extension must end with
  `return ctx.extension_metadata(reproducible = True)` and pin its fetches
  (sha256/commit). Otherwise `MODULE.bazel.lock` churns across OSes (see
  [docs/build-system.md](docs/build-system.md)).
- If you edit `MODULE.bazel`, build once and commit the resulting
  `MODULE.bazel.lock` change with it. The lockfile is what makes CI green
  mean "reproducible everywhere".
- The CAN spec (`lib/spec/*.textproto`) is format-enforced. After you edit
  it, run `bazel run //tools/spec:fmt` and keep `bazel test //lib/spec/...`
  green. Telemetry ids are append-only per group. A removed field gets a
  tombstone in `lib/spec/groups.textproto`, and its id is never used again
  (see [lib/spec/README.md](lib/spec/README.md)). `bazel run //tools/spec:editor`
  handles the bookkeeping for you. Generated CAN code
  (`//lib/codegen/cpp:can_lib`) is never edited or checked in.
- `lib/spec/wire.lock` is the one generated file that is committed, like
  `MODULE.bazel.lock`. It pins each telemetry field's number and derived
  proto type. Never hand-edit it. Run `bazel run //tools/spec:lock` and
  review the diff, since those lines are what deployed consumers and
  archived data can see.
- Comments explain *why*, not *what*. Match the surrounding density.
- `main` is always the current car. There are no per-year directories
  (see [CONTRIBUTING.md](CONTRIBUTING.md#season-policy)).
