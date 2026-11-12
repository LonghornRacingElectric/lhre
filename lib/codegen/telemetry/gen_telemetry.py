"""Generate telemetry bindings, CAN decoding, signal discovery, and Orion DDL."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from lib.codegen.telemetry import catalog
from lib.spec.ir import loader, validator, wire
from lib.codegen.telemetry import storage
from lib.spec.proto import can_spec_pb2
from lib.telemetry.contract import canonical_json, seal_manifest


SQL_TYPES = {
    "float": "real",
    "double": "double precision",
    "int32": "integer",
    "uint32": "bigint",
    "int64": "bigint",
    "uint64": "numeric",
    "bool": "boolean",
    "string": "text",
    "bytes": "bytea",
}


def snake_case(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def provenance(paths):
    digest = hashlib.sha256()
    for path in sorted(paths, key=str):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(Path(path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def checked_spec(paths):
    spec = loader.load(paths)
    errors, _ = validator.validate(spec)
    if errors:
        raise ValueError("invalid CAN spec:\n" + "\n".join(errors))
    return spec


def snapshot_fields(spec):
    types = wire.field_types(spec)
    ids = wire.field_ids(spec)
    fields = {}
    for key, (field_type, _) in types.items():
        fields.setdefault(key[0], []).append((ids[key], key[1], field_type))
    for group_fields in fields.values():
        group_fields.sort()
    return fields


def schema_manifest(spec, car="Orion"):
    groups = {group.name: group for _, group in spec.groups()}
    enums = {enum.name: enum for _, enum in spec.enum_types()}
    bindings = {}
    for _, message, signal, bit, telemetry in validator.telemetry_bindings(spec):
        bindings.setdefault((telemetry.group, telemetry.field), []).append((message, signal, bit, telemetry))
    external = {(group.name, field.name): field for group in groups.values() for field in group.external_field}
    fields = [{"path": [1], "names": ["time"], "type": "int64", "repeated": False, "unit": "ms", "layout": [], "enum": {}}]
    if car != "Artemis":
        fields.append({"path": [2], "names": ["packet_id"], "type": "int64", "repeated": False, "unit": "", "layout": [], "enum": {}})
    for (group_name, name), (field_type, count) in wire.field_types(spec).items():
        group = groups[group_name]
        repeated = field_type.startswith("repeated ")
        base_type = field_type.removeprefix("repeated ")
        layout, unit = [], ""
        if (group_name, name) in external:
            field = external[(group_name, name)]
            unit = field.unit
            layout = list(field.element_names)
            if repeated and not layout:
                raise ValueError(f"external array {group_name}.{name} requires element_names")
        else:
            entries = bindings[(group_name, name)]
            units = {signal.logical.physical.unit for _, signal, bit, _ in entries if bit is None}
            if len(units) > 1:
                raise ValueError(f"array {group_name}.{name} mixes units")
            unit = next(iter(units), "")
            if repeated:
                slots = {}
                for message, signal, bit, telemetry in entries:
                    slots.setdefault(message.name, []).append((message, signal, bit, telemetry))
                for message_entries in sorted(slots.values(), key=lambda values: values[0][0].can_id):
                    ordered = sorted(message_entries, key=lambda entry: entry[3].array_index)
                    for occurrence in range(validator.effective_quantity(ordered[0][0])):
                        for message, signal, bit, _ in ordered:
                            layout.append(f"{message.name}.{signal.name}.{bit.name if bit else ''}[{occurrence}]")
                if len(layout) != count:
                    raise ValueError(f"array {group_name}.{name} has an ambiguous layout")
        enum = enums.get(base_type)
        fields.append({
            "path": [group.snapshot_id, wire.field_ids(spec)[(group_name, name)]],
            "names": [snake_case(group_name), name],
            "type": base_type,
            "repeated": repeated,
            "unit": unit,
            "layout": layout,
            "enum": {str(value.number): value.name for value in enum.value} if enum else {},
        })
    return seal_manifest({
        "format_version": 1,
        "root": f"{car.lower()}.{car}SensorData",
        "fields": sorted(fields, key=lambda field: field["path"]),
        "groups": [
            {"name": group.name, "id": group.snapshot_id,
             "reserved_ids": sorted(group.reserved_ids), "reserved_names": sorted(group.reserved_names)}
            for group in sorted(groups.values(), key=lambda group: group.snapshot_id)
        ],
        "reserved_root_ids": sorted({number for spec_file in spec.files.values() for number in spec_file.groups.reserved_snapshot_ids} | ({2} if car == "Artemis" else set())),
        "reserved_root_names": sorted({name for spec_file in spec.files.values() for name in spec_file.groups.reserved_snapshot_names} | ({"packet_id"} if car == "Artemis" else set())),
        "enums": [
            {"name": enum.name, "reserved_numbers": sorted(enum.reserved_numbers), "reserved_names": sorted(enum.reserved_names)}
            for enum in sorted(enums.values(), key=lambda enum: enum.name)
        ],
    })


def reserved_lines(numbers, names):
    lines = []
    if numbers:
        lines.append("  reserved " + ", ".join(str(number) for number in sorted(numbers)) + ";")
    if names:
        lines.append("  reserved " + ", ".join(json.dumps(name) for name in sorted(names)) + ";")
    return lines


def render_proto(spec, digest, car="Orion"):
    fields = snapshot_fields(spec)
    groups = sorted((group for _, group in spec.groups()), key=lambda group: group.snapshot_id)
    lines = [
        "// Generated by //lib/codegen/telemetry:gen_telemetry — DO NOT EDIT.",
        f"// CAN spec SHA-256: {digest}",
        'syntax = "proto3";',
        f"package {car.lower()};",
        "",
        f"message {car}SensorData {{",
        "  int64 time = 1;",
        "  reserved 2;" if car == "Artemis" else "  int64 packet_id = 2;",
        '  reserved "packet_id";' if car == "Artemis" else "",
    ]
    for group in groups:
        lines.append(f"  {group.name} {snake_case(group.name)} = {group.snapshot_id};")
    for spec_file in spec.files.values():
        registry = spec_file.groups
        lines.extend(reserved_lines(registry.reserved_snapshot_ids, [snake_case(name) for name in registry.reserved_snapshot_names]))
    lines.extend(["}", ""])

    used_enums = set()
    scalars = set(SQL_TYPES)
    for group in groups:
        lines.append(f"message {group.name} {{")
        lines.extend(reserved_lines(group.reserved_ids, group.reserved_names))
        for field_id, name, field_type in fields.get(group.name, []):
            base_type = field_type.removeprefix("repeated ")
            if base_type not in scalars:
                used_enums.add(base_type)
            lines.append(f"  {field_type} {name} = {field_id};")
        lines.extend(["}", ""])

    enum_types = {enum.name: enum for _, enum in spec.enum_types()}
    for name in sorted(used_enums):
        enum = enum_types.get(name)
        if enum is None:
            raise ValueError(f"snapshot field references unknown proto type {name}")
        lines.append(f"enum {name} {{")
        lines.extend(reserved_lines(enum.reserved_numbers, enum.reserved_names))
        for value in sorted(enum.value, key=lambda value: value.number):
            lines.append(f"  {value.name} = {value.number};")
        lines.extend(["}", ""])
    if car == "Artemis":
        lines.extend([
            "message FieldPath { uint32 group = 1; uint32 field = 2; }",
            "message TelemetryUpdate { int64 sample_time_ns = 1; ArtemisSensorData values = 2; repeated FieldPath updated_fields = 3; }",
            "message TelemetryBatch { uint32 format_version = 1; repeated TelemetryUpdate updates = 2; }",
        ])
    return "\n".join(lines).rstrip() + "\n"




def signal_conv_type(signal):
    kind = signal.logical.WhichOneof("kind")
    if kind == "bitfield":
        return "bitfield"
    width = max(8, signal.encoding.bit_length)
    if width not in (8, 16, 32, 64):
        raise ValueError(f"BEVO decoder cannot represent {signal.name}'s {width}-bit width")
    prefix = "int" if signal.encoding.sign == can_spec_pb2.SIGNED else "uint"
    return f"{prefix}{width}"


def render_artemis_orm(spec):
    return storage.render_orm(storage.contract(spec))


def render_artemis_prisma(spec, common_sql):
    from apps.telemetry.scripts.generate_schema import PG_TO_PRISMA_TYPES, parse_sql_schema

    value = storage.contract(spec)
    lines = [
        'generator client {', '  provider = "prisma-client-js"',
        '  output = env("ARTEMIS_PRISMA_OUTPUT")', '}', '',
        'datasource db {', '  provider = "postgresql"', '  url = env("ARTEMIS_DATABASE_URL")', '}', '',
    ]
    for table in parse_sql_schema(storage.common_schema(common_sql)):
        lines.append(f"model {table['name']} {{")
        primary = [column['name'] for column in table['columns'] if column['is_pk']]
        for column in table['columns']:
            scalar = PG_TO_PRISMA_TYPES.get(column['type'], 'Float' if column['type'] == 'double' else 'String')
            suffix = '[]' if column['is_array'] else '?' if column['is_nullable'] and not column['is_pk'] else ''
            attributes = ' @id' if len(primary) == 1 and column['is_pk'] else ''
            if column['is_autoincrement']:
                attributes += ' @default(autoincrement())'
            lines.append(f"  {column['name']} {scalar}{suffix}{attributes}")
        if len(primary) > 1:
            lines.append('  @@id([' + ', '.join(primary) + '])')
        if not primary:
            lines.append('  @@ignore')
        lines.extend(['}', ''])
    types = {"double precision": "Float", "integer": "Int", "bigint": "BigInt", "smallint": "Int", "numeric": "Decimal", "boolean": "Boolean", "text": "String", "bytea": "Bytes"}
    tables = [(pool["table"], pool["fields"]) for pool in value["pools"]]
    for group in sorted({pool["group"] for pool in value["pools"]}):
        tables.append((group, [field for pool in value["pools"] if pool["group"] == group for field in pool["fields"]]))
    tables += [("sample_time", []), ("gps_precise", [{"name": name, "sql_type": kind} for name, kind in storage.GPS_FIELDS.items()])]
    for table, fields in tables:
        lines += [f"model {table} {{", "  session_start_ms BigInt", "  time BigInt"]
        raw = table not in {pool["group"] for pool in value["pools"]} | {"sample_time"}
        if raw:
            lines.append("  sample_time_ns BigInt")
        for field in fields:
            kind = types[field["sql_type"].removesuffix("[]")]
            suffix = "[]" if field["sql_type"].endswith("[]") else "?"
            lines.append(f"  {field['name']} {kind}{suffix}")
        lines += ["  @@id([session_start_ms, time, sample_time_ns])" if raw else "  @@id([session_start_ms, time])", "}", ""]
    lines += ["model recording_session {", "  session_start_ms BigInt @id", "  start_time BigInt", "  end_time BigInt", "  data_revision BigInt @default(0)", "  external_revision BigInt @default(0)", "  last_write_ms BigInt @default(0)", "}", ""]
    return "\n".join(lines)


def render_can_json(spec):
    field_types = wire.field_types(spec)
    bitfields = {bitfield.name: bitfield for _, bitfield in spec.bitfield_types()}
    packets = []
    for _, message in sorted(spec.messages(), key=lambda item: item[1].can_id):
        signals = []
        for index, signal in enumerate(message.signal):
            encoding = signal.encoding
            if encoding.byte_order == can_spec_pb2.BIG_ENDIAN:
                raise ValueError(f"BEVO decoder does not support BIG_ENDIAN signal {message.name}.{signal.name}")
            if encoding.start_bit % 8:
                raise ValueError(f"BEVO decoder requires byte-aligned signal {message.name}.{signal.name}")
            if encoding.offset:
                raise ValueError(f"BEVO decoder does not support offsets on {message.name}.{signal.name}")
            conv_type = signal_conv_type(signal)
            item = {
                "index": index,
                "start_byte": encoding.start_bit // 8,
                "name": signal.description or signal.name,
                "length": max(1, (encoding.bit_length + 7) // 8),
                "conv_type": conv_type,
                "precision": validator.effective_scale(encoding),
            }
            if signal.logical.WhichOneof("kind") == "boolean":
                item["is_boolean"] = True
            if signal.HasField("telemetry"):
                telemetry = signal.telemetry
                field_type = field_types[(telemetry.group, telemetry.field)][0]
                item["protobuf"] = {
                    "type": field_type.removeprefix("repeated "),
                    "field": telemetry.field,
                    "proto_id": telemetry.id,
                    "repeated": telemetry.repeated,
                    "field_index": telemetry.array_index if telemetry.repeated else None,
                }
            if conv_type == "bitfield":
                definition = bitfields[signal.logical.bitfield.bitfield_type]
                mappings = []
                for bit_index, bit in enumerate(definition.bit):
                    if bit.HasField("telemetry"):
                        mappings.append({
                            "protobuf_field": bit.telemetry.field,
                            "bit_index": bit_index,
                            "proto_id": bit.telemetry.id,
                        })
                item["bitfield_encoding"] = mappings
            signals.append(item)
        frequency = message.frequency_hz or None
        packets.append({
            "packet_id": message.can_id,
            "packet_name": message.description or message.name,
            "from": [message.from_board],
            "to": list(message.to_boards),
            "data_length": message.dlc,
            "frequency_ms": round(1000 / frequency) if frequency else None,
            "frequency": frequency,
            "quantity": validator.effective_quantity(message),
            "bus": message.bus,
            "bytes": signals,
        })
    return json.dumps(packets, indent=2) + "\n"


def render_artemis_sql(spec, digest, common_sql):
    value = storage.contract(spec)
    return storage.SESSION_SQL + storage.common_schema(common_sql) + "\n" + storage.update_sql(value)



def render_orm(spec):
    fields = snapshot_fields(spec)
    groups = sorted((group for _, group in spec.groups()), key=lambda group: group.snapshot_id)
    types = {
        "float": "REAL", "double": "DOUBLE_PRECISION", "int32": "Integer",
        "uint32": "BigInteger", "int64": "BigInteger", "uint64": "Numeric",
        "bool": "Boolean", "string": "Text", "bytes": "BYTEA",
    }
    lines = [
        '"""Generated Orion ORM definitions from //lib/spec."""',
        "from sqlalchemy import Column, Integer, BigInteger, Numeric, Boolean, Text, ForeignKey",
        "from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, REAL, DOUBLE_PRECISION",
        "from sqlalchemy.orm import relationship", "", "",
        "def declare_models(base):", "    class OrionPacket(base):",
        "        __tablename__ = 'packet'",
        "        __table_args__ = {'schema': 'public', 'extend_existing': True}",
        "        packet_id = Column(BigInteger, primary_key=True)",
        "        time = Column(BigInteger, nullable=False)",
    ]
    for group in groups:
        table = snake_case(group.name)
        lines.append(f"        {table} = relationship('Orion{group.name}', uselist=False, back_populates='packet')")
    for group in groups:
        table = snake_case(group.name)
        lines.extend([
            "", f"    class Orion{group.name}(base):",
            f"        __tablename__ = '{table}'",
            "        __table_args__ = {'schema': 'public', 'extend_existing': True}",
            "        packet_id = Column(BigInteger, ForeignKey('public.packet.packet_id'), primary_key=True)",
        ])
        for _, name, field_type in fields.get(group.name, []):
            scalar = types.get(field_type.removeprefix("repeated "), "Integer")
            if field_type.startswith("repeated "):
                scalar = f"ARRAY({scalar})"
            lines.append(f"        {name} = Column({scalar})")
        lines.append(f"        packet = relationship('OrionPacket', back_populates='{table}')")
    names = ["OrionPacket"] + [f"Orion{group.name}" for group in groups]
    lines.extend(["", "    return {" + ", ".join(f"'{name}': {name}" for name in names) + "}", ""])
    return "\n".join(lines)


def render_prisma(spec, common_sql):
    from apps.telemetry.scripts.generate_schema import PG_TO_PRISMA_TYPES, parse_sql_schema

    fields = snapshot_fields(spec)
    groups = sorted((group for _, group in spec.groups()), key=lambda group: group.snapshot_id)
    lines = [
        'generator client {', '  provider = "prisma-client-js"',
        '  output = env("ORION_PRISMA_OUTPUT")', '}', '',
        'datasource db {', '  provider = "postgresql"', '  url = env("ORION_DATABASE_URL")', '}', '',
    ]
    for table in parse_sql_schema(common_sql):
        lines.append(f"model {table['name']} {{")
        primary = [column['name'] for column in table['columns'] if column['is_pk']]
        for column in table['columns']:
            scalar = PG_TO_PRISMA_TYPES.get(column['type'], 'Float' if column['type'] == 'double' else 'String')
            suffix = '[]' if column['is_array'] else '?' if column['is_nullable'] and not column['is_pk'] else ''
            attributes = ' @id' if len(primary) == 1 and column['is_pk'] else ''
            if column['is_autoincrement']:
                attributes += ' @default(autoincrement())'
            lines.append(f"  {column['name']} {scalar}{suffix}{attributes}")
        if len(primary) > 1:
            lines.append('  @@id([' + ', '.join(primary) + '])')
        if not primary:
            lines.append('  @@ignore')
        lines.extend(['}', ''])
    lines.extend(['model packet {', '  packet_id BigInt @id', '  time BigInt'])
    for group in groups:
        table = snake_case(group.name)
        lines.append(f"  {table} {table}[] @ignore")
    lines.extend(['}', ''])
    types = {"float": "Float", "double": "Float", "int32": "Int", "uint32": "BigInt", "int64": "BigInt", "uint64": "Decimal", "bool": "Boolean", "string": "String", "bytes": "Bytes"}
    for group in groups:
        lines.extend([f"model {snake_case(group.name)} {{", "  packet_id BigInt"])
        for _, name, field_type in fields.get(group.name, []):
            scalar = types.get(field_type.removeprefix("repeated "), "Int")
            suffix = "[]" if field_type.startswith("repeated ") else "?"
            lines.append(f"  {name} {scalar}{suffix}")
        lines.extend(["  packet packet @relation(fields: [packet_id], references: [packet_id])", "  @@index([packet_id])", "  @@ignore", "}", ""])
    return "\n".join(lines)


def render_signals_json(spec):
    """One catalog row per snapshot field, including non-CAN producers."""
    types = wire.field_types(spec)
    candidates = {}
    for _, message, signal, bit, telemetry in validator.telemetry_bindings(spec):
        unit = signal.logical.physical.unit if signal.logical.WhichOneof("kind") == "physical" else ""
        candidates.setdefault((telemetry.group, telemetry.field), []).append({
            "module": message.from_board.lower() or None,
            "unit": unit,
            "rate_hz": telemetry.uplink_rate_hz if telemetry.HasField("uplink_rate_hz") else message.frequency_hz,
            "channel_type": can_spec_pb2.ChannelType.Name(telemetry.channel_type).lower(),
        })
    for _, group in spec.groups():
        for field in group.external_field:
            candidates[(group.name, field.name)] = [{
                "module": "gateway",
                "unit": field.unit if field.HasField("unit") else None,
                "rate_hz": field.uplink_rate_hz if field.HasField("uplink_rate_hz") else None,
                "channel_type": can_spec_pb2.ChannelType.Name(field.channel_type).lower(),
            }]
    signals = []
    for (group, field), (field_type, _) in sorted(types.items()):
        sources = candidates[(group, field)]
        # Shared producers and array slots still describe one protobuf field.
        # A single rate or module would be misleading when sources differ.
        metadata = {key: sources[0][key] if all(s[key] == sources[0][key] for s in sources) else None
                    for key in sources[0]}
        signals.append({
            "signal": f"{snake_case(group)}.{field}",
            "group": snake_case(group),
            "value_type": field_type.removeprefix("repeated "),
            "repeated": field_type.startswith("repeated "),
            **metadata,
        })
    return json.dumps(catalog.build(spec.car(), signals), indent=2, ensure_ascii=False) + "\n"


def render_sql(spec, digest, common_sql):
    fields = snapshot_fields(spec)
    groups = sorted((group for _, group in spec.groups()), key=lambda group: group.snapshot_id)
    lines = [
        common_sql.rstrip(),
        "",
        "-- Generated by //lib/codegen/telemetry:gen_telemetry — DO NOT EDIT.",
        f"-- CAN spec SHA-256: {digest}",
        "-- Generated Packet Table",
        "CREATE TABLE public.packet (",
        "    packet_id           bigint   NOT NULL,",
        '    "time"              bigint   NOT NULL,',
        "    CONSTRAINT packet_pk PRIMARY KEY (packet_id)",
        ");",
        "",
    ]
    for group in groups:
        table = snake_case(group.name)
        lines.extend([
            f"-- Generated {group.name} Table",
            f"CREATE TABLE public.{table} (",
            "    packet_id           bigint   NOT NULL,",
        ])
        for _, name, field_type in fields.get(group.name, []):
            repeated = field_type.startswith("repeated ")
            base_type = field_type.removeprefix("repeated ")
            sql_type = SQL_TYPES.get(base_type)
            if sql_type is None:
                sql_type = "integer"
            if repeated:
                sql_type += "[]"
            lines.append(f"    {name:20} {sql_type},")
        lines.extend([
            f"    CONSTRAINT fk_{table}_packet_id FOREIGN KEY(packet_id) REFERENCES packet(packet_id)",
            ");",
            "",
        ])
    lines.extend([
        "-- Generated Indexes",
        'CREATE INDEX IF NOT EXISTS idx_packet_time ON public.packet ("time" DESC);',
    ])
    for group in groups:
        table = snake_case(group.name)
        lines.append(f"CREATE INDEX IF NOT EXISTS idx_{table}_packet_id ON public.{table} (packet_id);")
    return "\n".join(lines).rstrip() + "\n"

def native_policy(spec):
    manifest = schema_manifest(spec, "Artemis")
    pools = {tuple(field["path"]): (pool["table"], pool["frequency_hz"]) for pool in storage.contract(spec)["pools"] for field in pool["fields"]}
    metadata = {}
    for _, _, _, _, binding in validator.telemetry_bindings(spec):
        metadata[(binding.group, binding.field)] = (binding.uplink_rate_hz if binding.HasField("uplink_rate_hz") else None, binding.channel_type)
    for _, group in spec.groups():
        for field in group.external_field:
            metadata[group.name, field.name] = (field.uplink_rate_hz if field.HasField("uplink_rate_hz") else None, field.channel_type)
    groups = {group.snapshot_id: group.name for _, group in spec.groups()}
    fields = []
    for field in manifest["fields"]:
        if len(field["path"]) != 2:
            continue
        rate, channel = metadata[groups[field["path"][0]], field["names"][1]]
        fields.append(dict(field, pool=pools[tuple(field["path"])][0], native_hz=pools[tuple(field["path"])][1], uplink_hz=rate,
                           channel=can_spec_pb2.ChannelType.Name(channel).lower()))
    return {"schema_id": manifest["schema_id"], "fields": fields}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("proto", "json", "can_json", "signals_json", "sql", "contract", "orm", "prisma", "storage", "storage_sql", "native_policy"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--car", choices=("Orion", "Artemis"), default="Orion")
    parser.add_argument("--common-sql", type=Path)
    parser.add_argument("spec", nargs="+")
    args = parser.parse_args()
    spec = checked_spec(args.spec)
    digest = provenance(args.spec)
    if args.kind == "proto":
        output = render_proto(spec, digest, args.car)
    elif args.kind in ("json", "can_json"):
        output = render_can_json(spec)
    elif args.kind == "signals_json":
        output = render_signals_json(spec)
    elif args.kind == "contract":
        output = canonical_json(schema_manifest(spec, args.car)) + "\n"
    elif args.kind == "native_policy":
        output = json.dumps(native_policy(spec), sort_keys=True, indent=2) + "\n"
    elif args.kind == "storage":
        output = json.dumps(storage.contract(spec), sort_keys=True, indent=2) + "\n"
    elif args.kind == "storage_sql":
        output = storage.update_sql(storage.contract(spec))
    elif args.kind == "orm":
        output = render_artemis_orm(spec) if args.car == "Artemis" else render_orm(spec)
    else:
        if args.common_sql is None:
            parser.error("--common-sql is required for SQL generation")
        if args.kind == "prisma":
            output = (render_artemis_prisma if args.car == "Artemis" else render_prisma)(spec, args.common_sql.read_text())
        else:
            output = (render_artemis_sql if args.car == "Artemis" else render_sql)(spec, digest, args.common_sql.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output)


if __name__ == "__main__":
    main()
