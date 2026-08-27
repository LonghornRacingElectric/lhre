# Build system: why it's built this way

This page is the decision record for the build system. The *how* lives in the
per-directory READMEs and the comments in [`MODULE.bazel`](https://github.com/LonghornRacingElectric/lhre/blob/main/MODULE.bazel)
and [`.bazelrc`](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc);
this page records the *whys* that would otherwise live only in commit messages
and the heads of whoever set it up. If you change the build system, add the
why here (or in a comment next to the change) — the person who understands it
graduates every year. The decision to use Bazel at all is summarized as
[ADR-002](architecture/002-bazel.md); this page is its long-form record.

## Why Bazel (and not CMake)

CMake is the embedded-industry default, and most vendor code (CubeMX, ST HAL
examples) assumes it. We chose Bazel anyway, for reasons that follow from
being a student team:

- **Hermetic toolchains beat setup instructions.** Members join every year,
  on macOS, Linux, and Windows laptops. With Bazel, ARM GCC, host LLVM,
  Python, OpenOCD, dfu-util, and clang-format are all pinned in
  `MODULE.bazel` and downloaded on first build — onboarding is
  `git clone && bazel test //...`. A CMake setup would replace that with a
  tools-installation README that drifts, breaks per-OS, and produces
  "works on my machine" bugs from mismatched compiler versions.
- **One graph builds host and MCU code together.** The LHAL design (see
  [drivers/lhal](../drivers/lhal/README.md)) means the same libraries compile
  for the host (unit tests, simulators) and for four STM32 families. Bazel's
  platforms and transitions make this first-class: `bazel test //...` builds
  every firmware image *and* runs every host test in one invocation, and
  [`firmware_project`](../tools/firmware/README.md) transitions each board to
  its family's platform automatically. CMake can only approximate this with
  separate build directories or ExternalProject superbuilds — cross-compiling
  and host-testing in one build is its weakest spot, and it's our core
  workflow.
- **Remote caching and execution.** Tests run on BuildBuddy's Linux
  executors and artifacts are shared through the remote cache, so a clean
  checkout on a slow laptop doesn't rebuild what CI already built. CMake has
  no equivalent.
- **One build for everything.** Firmware, host C++, Python tooling
  (flashing, formatting, codegen) live in one dependency graph with one
  command surface. No Make-wrapping-CMake-wrapping-scripts layering.

The cost, honestly: every vendor dependency needs a Bazel wrapper (see the
`*.BUILD` / `deps.bzl` files under [drivers/](../drivers/README.md)), IDE
integration needs a compile-commands extractor instead of coming for free,
and Bazel expertise is rarer than CMake exposure among incoming members.
That trade is deliberate — the wrapping is one-time work by a few
maintainers, while CMake's costs (environment drift, per-machine setup) are
paid continuously by everyone. This page exists to keep the one-time work
understandable.

## Where each decision is documented

| Decision | Why it's that way |
| -------- | ----------------- |
| MCU codegen flags (`-mcpu`/`-mfpu`/`-mfloat-abi`) baked into per-core toolchains, not targets | [toolchains/README](../toolchains/README.md) — every `cc_library` in a firmware graph gets the right float ABI with zero per-target plumbing |
| One target platform per STM32 family, custom `mcu_core` constraint | [platforms/README](../platforms/README.md) — `@platforms//cpu` is too coarse (m4f and m7f are both armv7e-m) |
| `//toolchains` targets tagged `manual`, macro mirrored locally | [toolchains/README](../toolchains/README.md) — untagged, `bazel build //...` downloads every host's ~150 MB GCC archive |
| Remote execution on Linux/macOS clients but not Windows; rc-file flag ordering | comments in [`.bazelrc`](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc) — Bazel can't reliably drive Linux executors from a Windows client, and `--enable_platform_specific_config` expands *before* plain `build` lines |
| Windows machines uncomment a short `--output_user_root` in `.bazelrc.user` | comments in [`.bazelrc`](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc) — importing the pip protobuf runtime from runfiles exceeds the 260-char `MAX_PATH`, rules_python 2.x ignores `--build_python_zip` (the old escape hatch) on Windows, and startup options can't be set per-OS in an rc file |
| Hermetic LLVM for host C++, registered before the BuildBuddy toolchain | comments in [`MODULE.bazel`](https://github.com/LonghornRacingElectric/lhre/blob/main/MODULE.bazel) — no dependency on Xcode/system GCC/MSVC, same clang everywhere |
| Single FreeRTOS kernel version for firmware and host sims | [drivers/freertos/README](../drivers/freertos/README.md) |
| Optimization level (`-Og`/`-Os`) keyed on `--compilation_mode` in the toolchain, not in target copts | [toolchains/README](../toolchains/README.md) — a hardcoded target-level `-O` silently overrides `-c opt` |
| `firmware_project` split into a legacy wrapper + symbolic macro; ST HAL packages non-public | [tools/firmware/README](../tools/firmware/README.md) — makes "app code never touches ST HAL" a build error instead of a review comment |
| Boards derive family/define/linker/startup from one `mcu` fact; BUILD files scaffolded by `tools/new_board.py` | [tools/firmware/README](../tools/firmware/README.md) — four part-coupled facts that compile fine when mismatched |
| `compile_commands.json` per-machine, regenerated not committed | [CONTRIBUTING](../CONTRIBUTING.md) — it embeds machine-specific toolchain paths |

## Whys recorded only here

### The `toolchains_arm_gnu` fork

`MODULE.bazel` pins a [LonghornRacingElectric fork](https://github.com/LonghornRacingElectric/toolchains_arm_gnu)
of upstream `toolchains_arm_gnu` via `git_override`. The fork carries fixes
upstream doesn't have (yet):

- **Fixed download URLs** — upstream's ARM toolchain URLs went stale.
- **Param-file support** — firmware link lines exceed Windows' command-length
  limit without it.
- **Flag/path handling fix in `config.bzl`** — upstream glued `-isystem` and
  its path into a single argument. Real arm-gcc silently tolerates that, but
  in `compile_commands.json` it made clangd lose all newlib/libstdc++ include
  paths and error out on firmware files.
- **`arm_toolchain` extension marked reproducible** — keeps it out of
  `MODULE.bazel.lock`; see "Module extensions stay out of the lockfile"
  below.

Prefer upstreaming fork changes when possible; either way, keep the pinned
commit in `MODULE.bazel` and this list in sync when you bump it.

### The hedron compile-commands fork and Windows patch

The extractor behind `//:refresh_compile_commands` is the helly25 fork of
hedron_compile_commands (the original is unmaintained), plus our own
`patches/hedron_windows_spawn_guard.patch`: its entry point calls `main()` at
module level, and Windows' `multiprocessing` spawn start method re-imports
the entry point in every worker, killing the process pool
(`BrokenProcessPool`). The patch adds an `if __name__ == "__main__":` guard.
Drop the patch if it gets upstreamed.

### Module extensions stay out of the lockfile

Every module extension in this repo returns
`ctx.extension_metadata(reproducible = True)`, and any new one must too.
Without it, Bazel records the extension's result in `MODULE.bazel.lock` —
and for extensions whose result depends on the host (`ctx.os`, like the
OpenOCD and dfu-util repos), each OS records a *different* result, so every
Windows/macOS/Linux build rewrote the lock in a permanent tug-of-war. Even
OS-independent extensions were observed to churn (the `toolchains_arm_gnu`
fork's `bzlTransitiveDigest` differed on Windows).

`reproducible = True` tells Bazel the extension needs no lock entry because
re-running it always yields an equivalent result. That claim is only honest
when everything the extension fetches is pinned — sha256 for archives, a
commit for `git_repository` — which is also this repo's rule anyway
(see Version pins below). So: **new module extension → pin every fetch,
then `return ctx.extension_metadata(reproducible = True)`** from the
implementation. Any of `tools/openocd/openocd.bzl`, `tools/dfu/dfu.bzl`,
`tools/debug/svd.bzl`, or the `drivers/*/deps.bzl` files is the worked
example. If a third-party extension ever churns the lock, patch it the
same way (`single_version_override(patches = ...)` for registry modules,
`patches` on the `git_override`, or commit it to our fork as was done for
`toolchains_arm_gnu`).

### Version pins

- **`MODULE.bazel.lock` is committed** — it pins registry resolution, so a
  green CI run means everyone resolves the same dependency versions. Bazel
  updates it automatically when `MODULE.bazel` changes; commit the two
  together.
- **googletest `1.17.0.bcr.2`** — Bazel 9 requires the BCR *patch* releases;
  plain `1.17.0` lacks the `load()` statements Bazel 9 needs. When bumping,
  always take the newest `.bcr.N` for a version.
- **protobuf `35.1`** — compiles the CAN spec meta-schema
  (`lib/spec/proto/can_spec.proto`). Pinned to the newest *stable* BCR
  release: `36.0-rc1`'s prebuilt `protoc` fails checksum verification
  (upstream re-uploaded the RC artifact) and `36.0-rc2` gates prebuilts
  behind a `-dev` guard. Safe to bump once a stable 36.x lands.

### Protobuf without compiling protobuf

The CAN spec pipeline (`lib/spec`, `lib/codegen`, and every board linking
the generated CAN library) needs `protoc` and a Python protobuf runtime at
build time. Firmware never links protobuf; the generated CAN library is
dependency-free C++.

Left to its defaults protobuf compiles both from source: the full protoc
plus much of abseil (~800 C++ actions), and separately the Python
runtime's `protoc_minimal`. That source build also fails outright on
Windows, because protobuf's io shims
(`using google::protobuf::io::win32::setmode` and friends) assume MSVC
headers while our hermetic clang targets MinGW, whose `<io.h>` declares
those legacy names itself. Accepting the cached source fallback was tried
and abandoned after repeated MinGW-only failures. Three pieces keep
source builds from happening at all:

1. `--incompatible_enable_proto_toolchain_resolution` (`.bazelrc`) turns
   on proto toolchain resolution, which lets the prebuilt `protoc`
   binaries protobuf registers take effect (gated on its
   `prefer_prebuilt_protoc` flag, default true). Without it Bazel uses
   the legacy wiring and compiles from source anyway.
   Caveat: that flag's machinery turned out to be broken in 36.0-rc1 (stale
   artifact hashes in its integrity file) and disabled in 36.0-rc2, so tools
   that need protoc directly (prost codegen in `apps/BEVO`) use
   `//tools/protoc` — our own correctly-pinned prebuilt, kept in version
   lockstep with this bazel_dep. When bumping protobuf, bump
   `tools/protoc/protoc.bzl` in the same change (and retire it if a stable
   36.x fixes the upstream pins).
2. `//toolchains/proto` registers a Python `proto_lang_toolchain` whose
   runtime is the pip `protobuf` wheel. The default
   `@protobuf//python:protobuf_python` is what drags in the
   `protoc_minimal` source build, prebuilts or not. Root-module toolchain
   registrations beat protobuf's own, so the pip runtime wins. The wheel
   must satisfy the version check protoc stamps into generated code
   (runtime >= gencode): protobuf `N.M` in `MODULE.bazel` pairs with pip
   `7.N.M` in `pyproject.toml`. Bump them together.
3. Tripwire flags in `.bazelrc` poison the compile line of any file from
   a protobuf external repo, so a regression fails loudly on every
   platform instead of surprising a Windows user later.

The pip wheel is also why Windows machines shorten `--output_user_root`
(see the table above). Its runfiles paths are what cross the 260-char
`MAX_PATH`.

### Build latency

Measured at ~70 targets on macOS. Windows runs the same phases roughly
3x slower from filesystem cost and Defender.

- **Warm server, no changes: ~1 s.** The steady-state floor. A no-op
  build much slower than this means something is wrong.
- **Cold server: ~12 s on macOS, tens of seconds on Windows.** Happens on
  the first build of the day, after `bazel shutdown`, or after the idle
  timeout. Nothing recompiles, since the on-disk action cache survives
  restarts. The cost is re-analyzing the graph, because the analysis
  cache lives only in server memory. `.bazelrc` sets the idle timeout to
  12 h so the server survives a work day.
- **Any flag change re-analyzes**, including switching to
  `--config=local`. Pick a config for the session.

Windows also reports about 3x the action count of macOS (~4.7k vs ~1.5k
for `//...`). That is the hermetic LLVM toolchain building its C/C++
runtime per OS. On Windows it compiles the whole mingw-w64 CRT plus
libc++ and libunwind from source, and mingw puts nearly every libc
function in its own file. On macOS it only builds compiler-rt against
Apple's system ABI. These actions re-run only on an LLVM toolchain bump,
and the remote cache means one machine pays per platform.

`--bes_upload_mode=fully_async` and `--remote_cache_async` keep builds
from blocking on BuildBuddy uploads at exit, which matters most on
Windows where everything executes locally. Locally you can exclude the
repo and `bazel info output_base` from Defender real-time scanning, the
biggest Windows speed lever, and build the target you are working on
instead of `//...`.