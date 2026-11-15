# ASan

`--config=asan` builds host C++ with AddressSanitizer: out-of-bounds reads
and writes, use after free, double free and leaks stop the test with a
report naming the line. UBSan ([tools/ubsan](../ubsan/README.md)) covers
arithmetic; this covers memory, which matters for the ring buffers, frame
packing and fixed arrays in firmware code that the host tests drive.

```bash
bazel test --config=asan //boards/... //drivers/... //lib/codegen/cpp/... //tools/asan:overflow_test
```

That is the command presubmit runs. Gotchas:

- **Host tests only**, like UBSan: the config sets `--build_tests_only` and
  skips the firmware size tests (tag `firmware_size`).
- **`:overflow_test` is the canary.** It reads past a heap buffer and
  passes only if ASan reports it, so the ASan step can't go green because
  the flags stopped reaching the compiler. Tagged `manual`; name it.
- **Turn it on with the toolchain's flag**, `--@llvm//config:asan=true`,
  as the config does. hermetic-llvm adds `-fsanitize=address` and puts the
  `libclang_rt.asan*` runtimes in the link's inputs only when that flag is
  set. A raw `--copt`/`--linkopt=-fsanitize=address` compiles fine and then
  fails to link with "cannot open libclang_rt.asan_static.a".
- ASan and UBSan are separate configs because each one recompiles
  everything it touches; the remote cache keeps repeats cheap.
