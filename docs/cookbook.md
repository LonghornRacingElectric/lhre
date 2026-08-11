# Cookbook: off the happy path

The [README](../README.md) covers the commands everyone figures out anyway.
This page is for the moments the convention doesn't cover you — the "how do
I…" questions that otherwise cost a Slack thread. Each recipe links the doc
that owns the details.

## The macro didn't pick up my source file

[`firmware_project`](../tools/firmware/README.md) globs three places:
`Core/` (CubeMX's), `Board/` (bring-up), and `App/**/*.cpp` (the app
library). A new `.cpp`/`.hpp` under one of those needs **no BUILD edit** —
if it's not being compiled, check it's really under one of them and spelled
`.cpp`, not `.cc`.

- A file that belongs somewhere else (a generated header, a source at the
  board root): `srcs` on the `firmware_project` call is additive —
  `srcs = ["extra.c"]` compiles it into the firmware alongside the globbed
  ones. `extra_includes` adds include dirs the same way.
- Never add files to `Core/` — it's 100% CubeMX-owned and regenerated from
  the `.ioc`. Hand-written code goes in `Board/` or `App/`.
- A board that has genuinely outgrown the App/ convention sets
  `enable_app = False` and hand-writes its own app targets, passing the
  library back in via `extra_deps` — the firmware half of the macro keeps
  working. See [tools/firmware](../tools/firmware/README.md).

## Adding a dependency

Where the `deps` line goes depends on who needs it:

- **App code** (`App/`): `app_deps` on the `firmware_project` call. It must
  build for the host too (that's what keeps the app testable), so in
  practice: `//drivers/lhal`-based or pure-logic libraries only.
  `app_deps = ["//drivers/longhorn"]` is the canonical example.
- **Firmware only** (bring-up code in `Board/`, needs no host build):
  `extra_deps` on the call — it lands on the `cc_binary`, not the app
  library.
- **New shared code** doesn't start life inside a board. Pure logic (ring
  buffers, CRC, CAN pack/unpack) is a plain `cc_library` + colocated
  `cc_test` under [lib/](../lib/README.md); board services that need
  peripherals go in [drivers/longhorn](../drivers/longhorn/README.md),
  written against LHAL so they stay host-testable.

If Bazel refuses with a **visibility error on `//drivers/stm32/...`**:
that's not a bug and the fix is not a visibility grant. App code can't
depend on ST HAL by design ([ADR-003](architecture/003-lhal.md)) — write
the LHAL interface instead (next recipe), or if it's genuinely one-off
bring-up code, do it in `Board/`, where ST HAL headers are already on the
include path.

## Vendoring a third-party library

Check the [Bazel Central Registry](https://registry.bazel.build) first. If
it's there:

1. `bazel_dep(name = ..., version = ...)` in `MODULE.bazel`.
2. Build once, and **commit the resulting `MODULE.bazel.lock` change
   together with `MODULE.bazel`** — the lockfile is what makes CI green
   mean "reproducible everywhere".

If it's not on the BCR (most embedded code isn't), the repo pattern is a
module extension that fetches a pinned commit and overlays our own BUILD
file, since upstream ships none. Worked examples to copy:
[`drivers/freertos/deps.bzl`](https://github.com/LonghornRacingElectric/lhre/blob/main/drivers/freertos/deps.bzl)
(single repo + `freertos_kernel.BUILD`) and
[`drivers/stm32/stm32g4/deps.bzl`](https://github.com/LonghornRacingElectric/lhre/blob/main/drivers/stm32/stm32g4/deps.bzl)
(several repos per family). The two rules that bite:

- **Pin every fetch** — sha256 for archives, a commit for
  `git_repository`.
- **End the extension with
  `return ctx.extension_metadata(reproducible = True)`** — without it,
  every OS records a different result in `MODULE.bazel.lock` and the
  lockfile churns forever. The why is in
  [build-system.md](build-system.md#module-extensions-stay-out-of-the-lockfile).

Then `use_repo` the result in `MODULE.bazel`, and give the new directory a
README. Upstream needs a fix? Prefer a patch in `patches/` (or a pinned
fork, as with `toolchains_arm_gnu`) over vendoring modified sources — see
[build-system.md](build-system.md) for both worked examples.

## Adding a peripheral to LHAL

The four-step recipe (interface → STM32 adapter → host fake → tests) is in
[drivers/lhal § Adding a peripheral abstraction](../drivers/lhal/README.md#adding-a-peripheral-abstraction).
Two things to know before starting:

- **You may not need to.** Every adapter exposes `handle()`, and
  peripherals without an abstraction (SPI, timers, ADC, …) can use ST HAL
  directly from `Board/` code — the escape hatch exists precisely so an
  interface is only written once app logic (the host-tested part) needs
  the peripheral.
- Interfaces are pure-virtual, heap-free, exception-free; callbacks are
  function pointer + context because they may run in ISR context on
  target.

## "My test target isn't found"

In order of likelihood:

1. **The file isn't directly in `App/`.** Only top-level `App/*_test.cpp`
   files become test targets. A `*_test.cpp` in a subdirectory
   (`App/foo/bar_test.cpp`) is worse than not found: it's excluded from the
   app library *and* doesn't become a test, so it silently isn't compiled
   at all. Move it up to `App/`.
2. **The name doesn't end in `_test.cpp`** (or `_sim.cpp` for simulators).
   Targets are synthesized purely from file names; the target is named by
   the file's stem (`App/vcu_app_test.cpp` → `:vcu_app_test`).
3. **The board opted out** — `enable_tests = False` (or `enable_sims =
   False`) in its `firmware_project` call keeps the files but stops
   synthesizing the targets.
4. **You're guessing the name.** List what actually exists:
   `bazel query 'kind(cc_test, //boards/VCU:all)'`.

Related: tests *run* on the remote Linux executors by default, so host
*binaries* (sims) need `bazel run --config=local //boards/VCU:vcu_sim` —
a plain `bazel run` of a sim tries to hand you a Linux binary on a Mac.

## Still stuck?

Paste the full Bazel error into an AI agent (Claude Code, Copilot, …) with
the repo open — decoding Bazel errors is something they're genuinely good
at, and this repo's checked-in [AGENTS.md](../AGENTS.md) briefs them on
the rules above so their fix suggestions respect the architecture. Which
is also why AGENTS.md is kept current: if an agent gives you advice that
contradicts these docs, fix the docs or the advice-giver's briefing as
part of your change.
