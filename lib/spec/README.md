# CAN spec

Single source of truth for every CAN message on the vehicle bus: wire
encoding (bit-level packing), logical meaning (units, enums, bitfields),
and telemetry mapping. Everything downstream — the firmware pack/unpack
library today ([gen/cpp](../codegen/cpp/README.md)), the snapshot proto, gateway
tables, DBC, and docs later — is generated from these files. Nothing
derived from the spec is hand-maintained; that's the point (see
[ADR-008](../../docs/architecture/008-can-spec-pipeline.md)).

## Files

| Path | Contents |
| ---- | -------- |
| `proto/can_spec.proto` | The meta-schema. This is code — review changes to it like code. |
| `buses.textproto` | Bus definitions (name, bitrate). |
| `types.textproto` | Shared enums and named bitfields. |
| `groups.textproto` | Telemetry group registry and append-only ID ledgers. |
| `messages/<board>.textproto` | All messages originating from one board. Enforced: every message's `from_board` must match its file (validator rule 5), which is what lets codegen emit one namespace + library per board. |

Every file parses as one `lhre.canspec.SpecFile`; each populates only the
sections that belong there. The current messages are **seed examples**
pending the one-time migration of the real message set from the
lhre-2026 CSVs (staged in-repo at `apps/BEVO/schema/` on the
`bevo-migration` branch).

## Editing workflow

1. Edit the `.textproto` (message layout is: one `signal` per field with
   `encoding` = where it sits in the frame, `logical` = what it is,
   `telemetry` = where it lands off-vehicle, omitted for bus-only
   signals).
2. Adding a telemetry field: take the group's `next_free_id` as the `id`,
   then bump `next_free_id` in `groups.textproto`.
3. Removing one: move its `id` to the group's `reserved_ids` and its
   `field` name to `reserved_names`. Never recycle either.
4. `bazel run //tools/spec:fmt` — rewrites your edits in canonical form.
5. `bazel test //lib/spec:...` — the validator explains anything you got
   wrong, including ledger mistakes and merge-conflict aftermath.

The ledger rules are checked by a single partition invariant (validator
rule 1): live ids ∪ reserved ids must be exactly `{1 … next_free_id−1}`,
disjoint. Forgotten tombstones, recycled ids, and two branches both
claiming the same id after a merge all fail loudly on a clean checkout —
no git history or previously generated output is consulted.

Spec files cannot carry hand-written comments: textproto comments don't
survive the canonical serializer. Prose goes in `description` fields,
which generators forward into C comments, DBC, and docs.

## Targets

```
bazel test //lib/spec:validate       # all semantic invariants; blocks merges
bazel test //lib/spec:format_check   # files match canonical serialization
bazel test //lib/spec:validator_test # unit tests for the invariants themselves
bazel run  //tools/spec:fmt      # rewrite spec files canonically
```

`//lib/spec:files` is the filegroup every generator consumes; `//lib/spec:ir`
(loader + validator + canonical serializer) is the only Python that
understands the format. The loader is deliberately tiny and isolated so a
future syntax swap stays an afternoon, not a rewrite.

## Indexed arrays (cell temps, cell voltages)

The lhre-2026 pattern — one layout repeated over a reserved ID range,
indexed by frame ID on the receive side — is expressed by composing two
existing attributes:

- `quantity: N` on the message reserves `[can_id, can_id + N)`; every
  frame in the block carries the same signal layout.
- Each per-frame slot binds the same repeated telemetry field:
  `telemetry { field: "cell_temps" id: K repeated: true array_index: <slot> }`.

The element a signal lands in is
`(frame_id − can_id) × slots_per_frame + array_index` — so 4 slots ×
`quantity: 4` is a 16-element array, one ledger id total. See
`HVC_CELL_TEMPS` in `messages/hvc.textproto` for the worked example; the
gateway generator implements the index math when it lands.

## Mux (supported, but off by convention)

The schema and generators support multiplexed messages (`mux_selector`,
`muxed`/`mux_value` — see `VCU_DEBUG`). Team convention is to prefer
more messages at lower frequency over muxing, since mux complicates
every downstream consumer; reach for it only when IDs are genuinely
scarce.

## Layering rules that bite

- `encoding` values are raw↔physical: `physical = raw * scale + offset`.
  That conversion happens once, off-board — firmware works in raw
  integers ([gen/cpp](../codegen/cpp/README.md)), telemetry consumers see only
  physical values.
- Rate tiers (`telemetry.rate_tier`, message `default_rate_tier`, or
  derived from `frequency_hz`) never affect proto structure or field
  ids. A tier change is a one-line diff.
- A signal's telemetry `id`/`field` are append-only per group; the wire
  layout is not — moving bits around is fine, firmware and gateway
  regenerate together.
