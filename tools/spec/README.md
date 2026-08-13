# Spec tooling

`bazel run //tools/spec:fmt` — rewrites every `lib/spec/**/*.textproto` in
canonical form (stable field order, messages sorted by CAN ID,
registries by name). The same serialization is enforced by
`//lib/spec:format_check`, and any tool that writes spec files must go
through the same module (`lib/spec/canonical.py`) so tool edits and hand
edits converge to identical bytes.

## Editor

`bazel run //tools/spec:editor` — local web UI for the spec (opens a
browser tab; `--port`/`--no-open` to taste). The backend imports the same
loader/validator/canonical modules as the build — no parallel logic — and
every save is validated with the real `//lib/spec` invariants and written
through the canonical serializer, so a save can never produce something
`bazel test //lib/spec/...` would reject. Saves are also refused if the
files changed on disk since the page loaded (another editor, `fmt`, a git
operation) — reload and redo instead of silently clobbering.

What it does:

- **Frame layout grid** per message with per-signal coloring; genuine
  overlaps show striped red, signals on different mux values legally
  share bits. (The grid's bit math mirrors the validator for display;
  the validator stays the authority — its errors show live.)
- **Live validation**: every edit re-runs all spec invariants; errors
  disable Save.
- **Ledger automation**: binding a signal to telemetry takes the group's
  `next_free_id` and bumps it; unbinding/deleting shrinks the high-water
  mark back if the id was the newest, and tombstones it into
  `reserved_ids`/`reserved_names` otherwise — including the bits of a
  bitfield type when its last referencing signal is deleted. Moving a
  binding between groups releases from one ledger and allocates from the
  other.
- **Physical-range preview** from bit length/sign/scale/offset, and
  enum/bitfield dropdowns from `types.textproto`.

Not covered (edit the files directly, the validator has your back):
buses, enum/bitfield *definitions*, and group descriptions — the editor
treats those as read-only reference data for now.

Implementation note: the backend is stdlib `http.server`, not the
FastAPI the original design sketch suggested — a localhost single-user
form tool doesn't need a framework, and every extra pip wheel is another
set of long runfiles paths for Windows to trip over (see
[docs/build-system.md](../../docs/build-system.md)).

Planned here: `migrate_csv` (one-time importer from the lhre-2026 CSVs).
