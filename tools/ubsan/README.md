# UBSan

`--config=ubsan` builds host C++ with `-fsanitize=undefined` in trap mode:
undefined behavior (signed overflow, out-of-range shifts, misaligned
loads, and the rest of the UBSan list) stops the process on the spot
instead of quietly producing a wrong number. That matters most for the
code that does bit math on CAN frames and sensor values, where UB tends to
look fine in one build and break in another.

```bash
bazel test --config=ubsan //boards/... //drivers/... //lib/codegen/cpp/... //tools/ubsan:trap_test
```

That is the command presubmit runs. Gotchas:

- **Trap mode, so no report.** The hermetic LLVM ships no sanitizer
  runtime, so there is nothing to print which check fired. A trapped test
  dies with `SIGILL`. Rerun it under a debugger to find the faulting
  line. ASan is not possible for the
  same reason.
- **Host tests only.** Firmware images can't take the flags, so the config
  sets `--build_tests_only` and skips each image's `{image}_size_test`
  (tag `firmware_size`, see [tools/firmware](../firmware/README.md)). Don't
  run `bazel build --config=ubsan //...`.
- **`:trap_test` is the canary.** It overflows a signed int on purpose
  and passes only if that traps, so the UBSan step can't go green just
  because the flags stopped reaching the compiler. It's tagged `manual`
  (it fails without the config), so wildcards skip it and it has to be
  named.
- The config changes `--copt`, so switching to it re-analyzes and
  recompiles everything it touches. The remote cache makes repeat runs
  cheap.
