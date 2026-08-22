# Formatting

```bash
bazel run //tools/format          # rewrite all tracked C/C++ files in place
bazel run //tools/format:check    # fail if anything needs formatting (CI runs this)
```

Both use clang-format from the hermetic `@llvm` toolchain with the repo-root
`.clang-format`, so every machine (and CI) formats identically — no system
clang-format involved.

Scope is `git ls-files` minus CubeMX-generated directories (`boards/*/Core/`,
`boards/*/Middlewares/`, `boards/*/USB_DEVICE/`, `boards/*/USB_Device/`;
regeneration would fight the formatter). Hand-written board code in
`boards/*/Board/` is formatted like everything else.
