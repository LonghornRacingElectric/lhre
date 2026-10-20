# CAN spec

Source of truth for every CAN message on the vehicle bus: wire encoding,
logical meaning, and telemetry mapping. Everything downstream is
generated from these files: the firmware library
([lib/codegen/cpp](../codegen/cpp/README.md)) and server schema bundle
([lib/codegen/telemetry](../codegen/telemetry/README.md)) today, with DBC
and richer gateway tables later. See
[ADR-008](../../docs/architecture/008-can-spec-pipeline.md).

## Files

| Path | Contents |
| ---- | -------- |
| `proto/can_spec.proto` | The meta-schema. Review changes to it like code. |
| `buses.textproto` | Bus definitions. |
| `types.textproto` | Shared enums and bitfields. |
| `groups.textproto` | Telemetry groups, root ids, field ledgers, and gateway-produced fields such as GPS. |
| `messages/<board>.textproto` | Messages sent by one source named by the lhre-2026 `From` column. `from_board` must match the filename, which is what lets codegen emit one namespace and library per source. |
| `wire.lock` | Generated, committed. See below. |
| `ir/` | The Python that reads, validates, and serializes the spec. |

Every file parses as one `lhre.canspec.SpecFile`. The 71-message base matrix
was migrated from lhre-2026 commit `d981057b8830f5f8be9c8b70a206dd2345748cd6`:
[can_packets.csv](https://github.com/LonghornRacingElectric/lhre-2026/blob/d981057b8830f5f8be9c8b70a206dd2345748cd6/drivers/longhorn-lib/config/can_packets.csv)
and
[can_bitfields.csv](https://github.com/LonghornRacingElectric/lhre-2026/blob/d981057b8830f5f8be9c8b70a206dd2345748cd6/drivers/longhorn-lib/config/can_bitfields.csv).
Their SHA-256 digests at migration were
`695d06925f3302c103b78ae52da8359d697e389235e36e8f3456c880b8ccd7a0`
and `418f03dd0061d3fe99586f429fafa6f9ff5c18eddaa4b5f59c8dc1c9d8a7ecc9`,
respectively.

The CSV needed explicit normalization where its rows could not satisfy the
typed spec:

- `FAN_FLOW_SPEEDS` uses `Data Acq`; that value was shifted into `Data[6]`.
- `BSE_3` uses `Critical`; that value was shifted into `Data[7]`.
- `HVC_BOUNDS_PARAMETERS` uses `Critical`; `ALLOW_BALANCE_COMMAND` already
  named `Critical`. Both have no defined payload, so their DLC is `0`.
- `CONTACTOR_STATUS` has four byte-aligned fields, so its source DLC `3` was
  corrected to `4`.
- Legacy `Quantity` was not an ID-range contract. Only cell voltages (`33`)
  and cell temperatures (`23`) occupy consecutive IDs; firmware-update rows
  are single IDs.
- Ambiguous thermal names use `ambient_temp` from `FAN_FLOW_SPEEDS` and
  `motor_temp` from `INVERTER_TEMPS_2`; their other CAN signals remain bus-only.
- Inverter telemetry preserves both legacy producers: `motor_speed` from
  `INVERTER_STATUS` (`0xA5`) and `INVERTER_SPEED` (`0xB0`), and
  `torque_feedback` from `INVERTER_TSO` (`0xAC`) and `INVERTER_SPEED`.
  These explicitly shared bindings keep updating even without `0xB0`.
- GPS fields have no CAN rows because the gateway populates them from NMEA.
  They remain live `Dynamics` fields in `groups.textproto`, alongside the
  gateway-produced `BoardStatus` fields.

The repository also retains the later `VCU_MODE_COMMAND` (`0x029`) addition
used by the telemetry gateway, for 72 generated runtime messages total. It has
no snapshot field and does not change the deployed protobuf surface.

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
bazel run //tools/spec:gen_docs # regenerate docs/can-matrix.md
```

The full message matrix and signal directory is published at
[docs/can-matrix.md](../../docs/can-matrix.md) (and auto-generated during doc builds).


`//lib/spec:files` is the filegroup generators consume. `//lib/spec:ir`
is the Python in `ir/`: loader, validator, canonical serializer, and wire
types. Nothing else understands the format. The loader is small and
isolated so a future syntax change stays cheap.

The vehicle has two 1 Mbit/s classic CAN buses: `Critical` for controls and
powertrain traffic, and `Data Acq` for CSM, USM, and TSM sensor traffic.

## Derived wire types

New CAN-backed fields derive their type from the encoding, and `ir/wire.py` is
the one implementation of the rule:

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

Fields already deployed by lhre-2026 retain their exact protobuf scalar type.
When that differs from the derived type, the telemetry binding carries a
`wire_type` compatibility override. Gateway-produced fields declare their type
in `groups.textproto`. Both appear in `wire.lock` and feed the same generators.

## wire.lock

`wire.lock` records every telemetry field as `<group>.<field> <id>
<type>`, plus each group's root `snapshot_id` and tombstones. Swapping root
group numbers changes the lock even if all nested fields stay unchanged.
It is generated, committed, and checked by
`//lib/spec:wire_lock_test`, the same way `MODULE.bazel.lock` works.
Regenerate with `bazel run //tools/spec:lock` and read the diff: those
lines are what deployed consumers and archived data can see.

The lock also records root group numbers and enum mappings/tombstones.
`GroupRegistry.next_snapshot_id` and its `reserved_snapshot_ids` /
`reserved_snapshot_names` protect removed root groups; enum definitions use
`reserved_numbers` and `reserved_names`. Generated protobuf emits these
reservations. CI compares the PR-base lock with the current lock using
`//tools/spec:check_wire_compat`; changing a deployed number, type or known
array layout requires a new field rather than editing the old entry.

External snapshot fields declare `unit` and, for arrays, ordered
`element_names`. GPS is latitude/longitude in degrees; GPS IMU has x/y/z
positions without an asserted physical unit. This metadata describes existing
gateway behavior and does not renumber or retype deployed fields. The first
registry version adds previously undocumented GPS array bounds to the lock.
See [telemetry compatibility](../telemetry/README.md).

The generated `.proto`, runtime `can.json`, and Orion database DDL stay Bazel
outputs under `//lib/codegen/telemetry` and are never committed.

## Shared scalar sources

Multiple CAN messages may update one scalar snapshot field when every
producer opts in with `telemetry.shared_source: true`. They must bind the same
group, field name and protobuf id, with identical raw width/sign, scale,
offset, logical meaning/unit and compatibility type override. Repeated fields,
bitfield bits and duplicate producers in one message cannot use this option.
Unmarked duplicate scalar bindings remain errors.

Each producer gets its own generated JSON mapping; protobuf and SQL still
declare the field once. The existing gateway uses the latest received value,
as with the legacy inverter mappings. There is no preferred-source switch,
timeout fallback or hardcoded CAN-id handling. Add or change shared bindings
in textproto, then run `fmt`, `lock` and the spec tests as usual.

## Indexed arrays

Cell temps and voltages use one layout repeated over an ID range,
indexed by frame ID:

- `quantity: N` reserves `[can_id, can_id + N)`. Every frame in the block
  has the same layout.
- Each slot binds the same repeated field:
  `telemetry { field: "cell_temps" id: K repeated: true array_index: <slot> }`.

Element index is `(frame_id − can_id) × slots_per_frame + array_index`,
so 4 slots with `quantity: 4` gives 16 elements on one ledger id.
`CELL_TEMPERATURES` in `messages/hvc.textproto` is the worked example.

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
