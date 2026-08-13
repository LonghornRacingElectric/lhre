# Spec tooling

`bazel run //tools/spec:fmt` — rewrites every `lib/spec/**/*.textproto` in
canonical form (stable field order, messages sorted by CAN ID,
registries by name). The same serialization is enforced by
`//lib/spec:format_check`, and any future tool that writes spec files (CSV
migrator, editor UI) must go through the same module
(`lib/spec/canonical.py`) so tool edits and hand edits converge to identical
bytes.

Planned here, per the pipeline design: `migrate_csv` (one-time importer
from the lhre-2026 CSVs) and `editor` (local web UI over the same
loader/validator).
