"""Generate the deployed telemetry proto, CAN decode JSON, and Orion DDL."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from lib.spec.ir import loader, validator, wire
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


def schema_manifest(spec):
    groups = {group.name: group for _, group in spec.groups()}
    enums = {enum.name: enum for _, enum in spec.enum_types()}
    bindings = {}
    for _, message, signal, bit, telemetry in validator.telemetry_bindings(spec):
        bindings.setdefault((telemetry.group, telemetry.field), []).append((message, signal, bit, telemetry))
    external = {(group.name, field.name): field for group in groups.values() for field in group.external_field}
    fields = [
        {"path": [1], "names": ["time"], "type": "int64", "repeated": False, "unit": "ms", "layout": [], "enum": {}},
        {"path": [2], "names": ["packet_id"], "type": "int64", "repeated": False, "unit": "", "layout": [], "enum": {}},
    ]
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
        "root": "orion.OrionSensorData",
        "fields": sorted(fields, key=lambda field: field["path"]),
        "groups": [
            {"name": group.name, "id": group.snapshot_id,
             "reserved_ids": sorted(group.reserved_ids), "reserved_names": sorted(group.reserved_names)}
            for group in sorted(groups.values(), key=lambda group: group.snapshot_id)
        ],
        "reserved_root_ids": sorted(number for spec_file in spec.files.values() for number in spec_file.groups.reserved_snapshot_ids),
        "reserved_root_names": sorted(name for spec_file in spec.files.values() for name in spec_file.groups.reserved_snapshot_names),
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


def render_proto(spec, digest):
    fields = snapshot_fields(spec)
    groups = sorted((group for _, group in spec.groups()), key=lambda group: group.snapshot_id)
    lines = [
        "// Generated by //lib/codegen/telemetry:gen_telemetry — DO NOT EDIT.",
        f"// CAN spec SHA-256: {digest}",
        'syntax = "proto3";',
        "package orion;",
        "",
        "message OrionSensorData {",
        "  int64 time = 1;",
        "  int64 packet_id = 2;",
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
        'datasource db {', '  provider = "postgresql"', '  url = env("TELEMETRY_DATABASE_URL")', '}', '',
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


def render_json(spec):
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("proto", "json", "sql", "contract", "orm", "prisma"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--common-sql", type=Path)
    parser.add_argument("spec", nargs="+")
    args = parser.parse_args()
    spec = checked_spec(args.spec)
    digest = provenance(args.spec)
    if args.kind == "proto":
        output = render_proto(spec, digest)
    elif args.kind == "json":
        output = render_json(spec)
    elif args.kind == "contract":
        output = canonical_json(schema_manifest(spec)) + "\n"
    elif args.kind == "orm":
        output = render_orm(spec)
    else:
        if args.common_sql is None:
            parser.error("--common-sql is required for SQL generation")
        output = render_prisma(spec, args.common_sql.read_text()) if args.kind == "prisma" else render_sql(spec, digest, args.common_sql.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output)


if __name__ == "__main__":
    main()
