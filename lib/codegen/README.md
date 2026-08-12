# Generated artifacts

Code generators that consume the validated CAN spec
([spec/](../spec/README.md)). One directory per output; each is a
hermetic Bazel rule whose only inputs are `//spec:files` and the
generator tool, so everything caches and nothing needs a checked-in
copy.

| Directory | Output | Status |
| --------- | ------ | ------ |
| [cpp/](cpp/README.md) | Firmware pack/unpack library (`//lib/codegen/cpp:can_lib`) | Done |
| proto/ | Telemetry snapshot proto | Planned |
| gateway/ | Gateway decode + snapshot assignment tables | Planned |
| ingest/ | Presence-aware ingest helpers + DDL | Planned |
| dbc/ | DBC export for commercial CAN tooling | Planned |
| docs/ | Markdown signal reference for the docs site | Planned |

Generated sources carry a provenance header: the spec files and a
content hash of them, so a stale artifact is identifiable at a glance.
