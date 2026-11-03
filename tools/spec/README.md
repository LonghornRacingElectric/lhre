# Spec tooling

```bash
bazel run //tools/spec:editor   # web UI for editing the spec
bazel run //tools/spec:fmt      # rewrite lib/spec/*.textproto canonically
bazel run //tools/spec:lock     # regenerate lib/spec/wire.lock
bazel run //tools/spec:gen_docs # regenerate docs/can-matrix.md
bazel run //tools/spec:check_wire_compat -- --baseline /path/to/previous-wire.lock --current "$PWD/lib/spec/wire.lock"
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

Telemetry ids are handed out automatically. Removing the last binding to a
field tombstones its number and name, including the highest allocated id; the
high-water mark never shrinks. Shared scalar fields retain their ledger entry
while another producer still uses them. Configure shared producers in textproto;
the editor preserves `shared_source` and validates matching contracts on save.
New groups allocate `snapshot_id` from `next_snapshot_id` and
removed groups reserve their root number/name. Removed enum values also
tombstone their number/name, and new enum values skip those numbers.

CI compares the PR-base wire lock through `check_wire_compat`; updating the
lock alone cannot approve a deployed tag/type change. Retiring a whole root
group reserves its entire numeric subtree.

The signal table shows the derived snapshot proto type per signal, so
you can see when a scale change turns a `uint32` into a `float`. The
type comes from `lib/spec/ir/wire.py` over the API; the page does not
compute it.

Backend is stdlib `http.server`. A localhost form tool does not need a
framework, and extra pip wheels cost us Windows path length.

The in-repo textproto is authoritative; current builds have no CSV fallback.
