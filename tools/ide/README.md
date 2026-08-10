# IDE

Regenerates `compile_commands.json`, which points clangd (and anything built
on it — VS Code, CLion, Neovim) at the right compiler flags and header trees.
Run this whenever C++ highlighting breaks: red squiggles, "Unknown type name
'uint32_t'", unresolved includes.

Two entry points, both run from anywhere in the repo:

```bash
bazel run //:refresh_ide
```

The **reliable, slow** path. Builds `//...` with `--remote_download_all`
first, then extracts compile commands. The build step matters because the
compile commands reference generated header trees under `bazel-out`
(hermetic libc++ `__config_site`, mingw crt headers, firmware `build_info`,
…) that the extractor alone never creates — it only aqueries the action
graph. And a plain `bazel build` isn't enough either: with the remote cache,
Bazel's default "build without the bytes" mode skips materializing
intermediate outputs on cache hits, leaving those directories as phantom
metadata. `--remote_download_all` forces them onto disk.

```bash
bazel run //:refresh_compile_commands
```

The **fast** path: extraction only, no build. Use it when the build outputs
are already on disk (e.g. you've been building locally) — it won't
materialize missing generated headers, so if squiggles persist afterwards,
fall back to `//:refresh_ide`.

Both extract with `--config=local` baked in: remote-exec-configured actions
reference Linux-hosted toolchains the extractor can't execute on macOS or
Windows, while locally-configured actions use this machine's hermetic
toolchains, so header discovery works everywhere.
