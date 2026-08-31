# apps

Host-side software: telemetry receivers, dashboards, CAN gateways — code
that runs on a laptop or pit computer, not on the car.

One subdirectory per app, each with a `README.md` covering what it does and
how to run it (host binaries run via `bazel run --config=local`).

- [BEVO](BEVO/README.md) — on-car telemetry computer: CAN decode, driver
  display, logging, MQTT uplink (Rust).
- [telemetry](telemetry/README.md) — server side: MQTT ingest, Kafka,
  processors, Postgres, the analysis webtool (Python, Go, TypeScript).
  Container images build on Linux only; see
  [BAZEL_BUILD.md](telemetry/BAZEL_BUILD.md).
