"""Module extension to fetch the FreeRTOS kernel from GitHub.

Family-agnostic: the same kernel checkout provides the Cortex-M ports
compiled into firmware and the POSIX/Windows simulator ports compiled into
host tests and sims. Fetched here (not per-STM32-family) so there is exactly
one kernel version everywhere.
"""

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

def _freertos_deps_impl(ctx):
    git_repository(
        name = "freertos_kernel",
        remote = "https://github.com/FreeRTOS/FreeRTOS-Kernel.git",
        # V11.1.0
        commit = "dbf70559b27d39c1fdb68dfb9a32140b6a6777a0",
        build_file = "//drivers/freertos:freertos_kernel.BUILD",
        # POSIX simulator port: a task cancelled while suspended in
        # event_wait() dies holding its event mutex (pthread_cond_wait is a
        # cancellation point that re-acquires the mutex), deadlocking
        # vTaskEndScheduler() on glibc. Still unfixed upstream as of V11.2.0.
        patches = ["//patches:freertos_posix_event_wait_cancel.patch"],
        patch_args = ["-p1"],
    )

    # Commit-pinned, so keep it out of MODULE.bazel.lock (lock churn).
    return ctx.extension_metadata(reproducible = True)

freertos_deps = module_extension(
    implementation = _freertos_deps_impl,
)
