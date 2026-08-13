# CAN spec

Source of truth for every CAN message on the vehicle bus: wire encoding,
logical meaning, and telemetry mapping. Everything downstream is
generated from these files: the firmware library
([lib/codegen/cpp](../codegen/cpp/README.md)) today, plus the snapshot
proto, gateway tables, and DBC later. See
[ADR-008](../../docs/architecture/008-can-spec-pipeline.md).

## Files

| Path | Contents |
| ---- | -------- |
| `proto/can_spec.proto` | The meta-schema. Review changes to it like code. |
| `buses.textproto` | Bus definitions. |
| `types.textproto` | Shared enums and bitfields. |
| `groups.textproto` | Telemetry groups and their id ledgers. |
| `messages/<board>.textproto` | Messages sent by one board. `from_board` must match the filename, which is what lets codegen emit one namespace and library per board. |
| `wire.lock` | Generated, committed. See below. |
| `ir/` | The Python that reads, validates, and serializes the spec. |

Every file parses as one `lhre.canspec.SpecFile`. Current messages are
seed examples, pending migration of the real set from the lhre-2026 CSVs
(staged at `apps/BEVO/schema/` on the `bevo-migration` branch).

Spec files carry no comments. Textproto comments do not survive the
canonical serializer, so prose goes in `description` fields, which the
generators forward into generated code and docs.

## Editing

Use `bazel run //tools/spec:editor`, or edit the files and run
`bazel run //tools/spec:fmt`. Either way `bazel test //lib/spec/...`
explains what you got wrong.

Telemetry ids are append-only per group. Take `next_free_id` when adding
a field and bump it; when removing one, move its id to `reserved_ids` and
its name to `reserved_names`. The editor does this for you.

Validator rule 1 checks the ledger with one invariant: live ids plus
reserved ids must equal exactly `{1 … next_free_id−1}`, disjoint. That
catches forgotten tombstones, recycled ids, and two branches claiming the
same id after a merge, all on a clean checkout with no reference to git
history.

## Targets

```bash
bazel test //lib/spec/...       # validate, format, wire lock, unit tests
bazel run //tools/spec:editor   # web UI
bazel run //tools/spec:fmt      # canonical formatting
bazel run //tools/spec:lock     # regenerate wire.lock
```

`//lib/spec:files` is the filegroup generators consume. `//lib/spec:ir`
is the Python in `ir/`: loader, validator, canonical serializer, and wire
types. Nothing else understands the format. The loader is small and
isolated so a future syntax change stays cheap.

## Derived wire types

Nothing in the spec declares a field's type. It falls out of the
encoding, and `ir/wire.py` is the one implementation of the rule:

| Logical kind | Condition | Proto type |
| --- | --- | --- |
| `boolean` | | `bool` |
| `bitfield` | | `bool` per bit |
| `enum_type` | | the generated enum |
| `physical` | `scale == 1`, integral `offset` | smallest `int32`/`uint32`/`int64`/`uint64` holding the range |
| `physical` | scaled, `bit_length ≤ 24` | `float` |
| `physical` | scaled, `bit_length > 24` | `double` |

Unscaled signals become integers because the raw value already is the
physical value. The float/double split comes from float32's 24-bit
mantissa; a wider raw field would collapse distinct values, which is what
bites GPS at `scale: 1e-7`.

Editing `scale` therefore changes a live field's wire type while its id
stays the same. The ledger cannot see that, which is what `wire.lock` is
for.

## wire.lock

`wire.lock` records every telemetry field as `<group>.<field> <id>
<type>`, plus tombstones. It is generated, committed, and checked by
`//lib/spec:wire_lock_test`, the same way `MODULE.bazel.lock` works.
Regenerate with `bazel run //tools/spec:lock` and read the diff: those
lines are what deployed consumers and archived data can see.

The generated `.proto` stays a build output and is never committed.

## Indexed arrays

Cell temps and voltages use one layout repeated over an ID range,
indexed by frame ID:

- `quantity: N` reserves `[can_id, can_id + N)`. Every frame in the block
  has the same layout.
- Each slot binds the same repeated field:
  `telemetry { field: "cell_temps" id: K repeated: true array_index: <slot> }`.

Element index is `(frame_id − can_id) × slots_per_frame + array_index`,
so 4 slots with `quantity: 4` gives 16 elements on one ledger id.
`HVC_CELL_TEMPS` in `messages/hvc.textproto` is the worked example.

## Mux

Multiplexed messages work (`mux_selector`, `muxed`, `mux_value`; see
`VCU_DEBUG`), but team convention is to add more messages at lower
frequency instead. Mux complicates every downstream consumer. Use it only
when IDs are scarce.

## Rules that bite

- `physical = raw * scale + offset`, applied once at the gateway.
  Firmware works in raw integers, servers see physical values.
- Rate tiers never affect proto structure or ids. Changing a tier is a
  one-line diff.
- Telemetry ids are append-only. Bit layout is not, so moving bits around
  is fine since firmware and gateway regenerate together.
