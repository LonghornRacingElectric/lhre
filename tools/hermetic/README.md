# Hermetic tests

```bash
bazel test //tools/hermetic:network_off_test
```

Tests here run without network: `.bazelrc` sets
`test --sandbox_default_allow_network=false`, so a sandboxed test gets an
empty network namespace with only loopback. A unit test that reaches the
internet or a service passes on one machine and fails on the next, and it
fails silently the day the service changes; [docs/testing.md](../../docs/testing.md)
says to fake the boundary instead.

`:network_off_test` is the canary, like
[`//tools/ubsan:trap_test`](../ubsan/README.md). On Linux it fails if the
test can see any network interface but loopback, which holds whether or not
the machine itself is online. Everywhere it also fails if a test can connect
to a public address or resolve a public name, and checks that loopback still
works (tests may start a server on `127.0.0.1`). It's tagged `external` and
`no-cache` so neither Bazel's local result cache nor the remote cache serves
it: the sandbox flag isn't part of the cache key, and a cached pass would hide
the setting being removed. It runs in
`bazel test //...` on Linux and macOS, locally and on the remote executors,
so CI goes red if the setting ever stops reaching tests.

Gotchas:

- **Windows has no sandbox,** so Bazel can't take the network away there.
  The canary is incompatible with Windows and skipped; the Linux CI job is
  the one that enforces the rule.
- **A test that really needs a service** gets `tags = ["requires-network"]`
  and belongs in an integration suite, not a unit test. The telemetry
  Docker suites are tagged `local` + `manual` and run in their own CI job.
- **`--spawn_strategy=local` (or `standalone`) turns the sandbox off,** and
  the canary will fail. That's it doing its job.
