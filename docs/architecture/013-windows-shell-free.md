# ADR-013: Windows builds are shell-free

- **Status:** Accepted
- **Date:** 2026-10

## Context

[ADR-002](002-bazel.md) chose Bazel so that onboarding is
`git clone && bazel test //...` on any laptop. On Windows that had stopped
being true: members were installing MSYS2 before the build would run.

Bazel has no shell of its own on Windows. It looks for `bash.exe` through
`BAZEL_SH` and `PATH`, and it wants one in more places than is obvious:

- A `genrule` with only `cmd`, and any `ctx.actions.run_shell`, runs under
  bash. Four of our genrules did, plus one action inside protobuf
  (`ProtocAuthenticityCheck`, a validation action on every `proto_library`).
- Every test rule implicitly depends on
  `@bazel_tools//tools/test:collect_coverage`, an `sh_binary`. With no bash
  found, an `sh_binary` fails at analysis, so `bazel test` stops before
  running anything.
- The shell's directory goes on every action's `PATH`, and `PATH` is part of
  the remote cache key.

Git for Windows ships a bash but leaves it off `PATH`, so a laptop with only
the README's prerequisites had none. What Bazel found depended on the
machine: nothing on some, the WSL launcher on others, MSYS2 or Git Bash
where someone had set it up. Because of the third point, machines whose
bash lived in different places did not share cache hits with each other or
with CI.

The dependency was narrow. Of roughly 7,300 actions in `//...` on Windows,
six invoked bash. The 21 `sh_binary` / `sh_test` targets in the repo were
already marked non-Windows.

## Decision

Nothing that builds or tests on Windows may need a shell, and the build is
configured so it cannot quietly start needing one:

- `.bazelrc` pins `--shell_executable` on Windows to a path that exists on
  no machine. An action that wants bash fails the same way everywhere, CI
  included, and every Windows machine computes the same cache keys.
- `//toolchains/sh` registers a Windows shell toolchain carrying that same
  path, so test rules analyze with no bash installed.
- Build steps exec their tool directly: a rule calling `ctx.actions.run`,
  `run_binary`, or a genrule that also sets `cmd_bat`.
- `bazel run` wrappers are native launchers from `hermetic_launcher`, not a
  generated `.cmd` plus a bash script. The flash targets are the first use.
- protobuf's check is patched to do its comparison through `cmd.exe` on
  Windows hosts. It still runs.
- Real shell scripts (`sh_binary`, `sh_test`) stay `target_compatible_with`
  non-Windows.

The mechanics, and what to do with the patch on a protobuf bump, are in
[build-system.md](../build-system.md#windows-builds-are-shell-free).

## Alternatives considered

- **Document MSYS2 or Git Bash as a prerequisite.** Where things were
  heading. It is one more thing to install and get onto `PATH` correctly, on
  the OS where setup already has the most steps, and it leaves cache keys
  depending on where each person put it.
- **Point Bazel at the bash inside Git for Windows.** Nothing to install,
  since Git is already required. But its location is per-machine, so every
  laptop needs its own `BAZEL_SH` or `--shell_executable` line, which is the
  setup step again.
- **Send Windows members to WSL2.** Already the answer for remote
  execution. It means a second checkout and toolchain, and flashing or
  debugging a board from inside WSL needs USB passthrough set up first.
- **Turn validation actions off on Windows** (`--norun_validations`) instead
  of patching protobuf. One line and no patch to carry, but it disables the
  check on which `protoc` we run, along with every other validation action.
- **Keep generating `.cmd` and bash wrappers for `bazel run` targets.** They
  never needed bash on Windows, so replacing them was not required for the
  goal. But two templates per rule have to be kept in step by hand, and both
  found their tool through a runfiles tree beside the wrapper, which exists
  only when the wrapper is the top-level target.

## Consequences

- MSYS2 and Git Bash are not prerequisites. A Windows laptop is back to the
  two in the README: Bazelisk and Git.
- A new genrule without `cmd_bat`, or a rule using `run_shell`, fails on
  Windows with the pinned path in the error. That is the tripwire working.
  The fix is to make the step shell-free or mark the target non-Windows,
  never to install bash.
- `sh_binary` and `sh_test` cannot run on Windows, and neither can
  `bazel coverage`, whose collection script is one.
- We carry a patch against protobuf. A protobuf bump has to confirm it still
  applies; it goes away when upstream stops using `run_shell` there.
- `hermetic_launcher` is a pre-1.0 dependency. Its launchers embed at most
  ten arguments of 256 bytes each, which the flash rules are well inside.
- Linux and macOS keep bash. The launcher is the only change they see, and
  targets Windows never builds can stay plain genrules.
