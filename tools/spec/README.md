# Spec tooling

```bash
bazel run //tools/spec:editor   # web UI for editing the spec
bazel run //tools/spec:fmt      # rewrite lib/spec/*.textproto canonically
bazel run //tools/spec:lock     # regenerate lib/spec/wire.lock
bazel run //tools/spec:gen_docs # regenerate docs/can-matrix.md
```

`fmt` and `lock` are enforced by `//lib/spec:format_check` and
`//lib/spec:wire_lock_test`, so CI tells you when to run them. The CAN Matrix
page is automatically generated as an MkDocs pre-build hook or via `gen_docs`.


## Editor

Opens a browser tab with three views:

- **Messages**: message fields, a bit grid for the frame, and a signal
  list. Click a signal to edit it inline. The grid stripes red where two
  signals claim a bit, allowing for mux.
- **Types**: create and edit enums and bitfields, including the
  telemetry binding on each bitfield bit.
- **Groups**: telemetry groups, their ledger state, and which fields are
  bound to them.

Every edit re-validates against the real `//lib/spec` invariants, so
errors show up in the right panel before you save. Saving writes through
the canonical serializer and refuses if the files changed on disk since
the page loaded.

Telemetry ids are handed out and released automatically. Removing a
binding shrinks `next_free_id` if the id was the newest, otherwise it is
tombstoned.

The signal table shows the derived snapshot proto type per signal, so
you can see when a scale change turns a `uint32` into a `float`. The
type comes from `lib/spec/ir/wire.py` over the API; the page does not
compute it.

Backend is stdlib `http.server`. A localhost form tool does not need a
framework, and extra pip wheels cost us Windows path length.

Planned: `migrate_csv`, a one-time importer from the lhre-2026 CSVs.
