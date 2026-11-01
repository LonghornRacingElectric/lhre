"""Build-time schema identities and definition-level compatibility checks."""

import hashlib
import json

ROOT_PATHS = {(1,), (2,)}
SCALAR_TYPES = {
    "double": 1, "float": 2, "int64": 3, "uint64": 4, "int32": 5,
    "bool": 8, "string": 9, "bytes": 12, "uint32": 13,
}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_id(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def seal_manifest(manifest):
    manifest = json.loads(canonical_json(manifest))
    manifest.pop("schema_id", None)
    manifest["fields"] = sorted(manifest["fields"], key=lambda field: field["path"])
    for name, key in (("groups", "id"), ("enums", "name")):
        if name in manifest:
            manifest[name].sort(key=lambda item: item[key])
            for entry in manifest[name]:
                for reserved in ("reserved_ids", "reserved_names", "reserved_numbers"):
                    if reserved in entry:
                        entry[reserved].sort()
    for reserved in ("reserved_root_ids", "reserved_root_names"):
        if reserved in manifest:
            manifest[reserved].sort()
    return dict(manifest, schema_id=content_id(manifest))


class Manifest:
    def __init__(self, manifest):
        if isinstance(manifest, bytes):
            manifest = json.loads(manifest)
        unsigned = {key: value for key, value in manifest.items() if key != "schema_id"}
        canonical = seal_manifest(unsigned)
        if manifest.get("format_version") != 1 or manifest.get("schema_id") != canonical["schema_id"]:
            raise ValueError("invalid schema identity or manifest version")
        manifest = self.manifest = canonical
        self.schema_id = manifest["schema_id"]
        self.fields = {tuple(field["path"]): field for field in manifest["fields"]}
        if len(self.fields) != len(manifest["fields"]):
            raise ValueError("duplicate field paths")
        for path, field in self.fields.items():
            if len(path) not in (1, 2) or len(field["names"]) != len(path):
                raise ValueError("invalid field path")
            if any(type(number) is not int or not 0 < number < 536870912 or 19000 <= number <= 19999 for number in path):
                raise ValueError("invalid protobuf field number")
        for path, name in (((1,), "time"), ((2,), "packet_id")):
            field = self.fields.get(path, {})
            if field.get("names") != [name] or field.get("type") != "int64" or field.get("repeated"):
                raise ValueError("incompatible telemetry root")


class Schema(Manifest):
    def __init__(self, manifest, descriptor_set):
        from google.protobuf import descriptor_pb2, descriptor_pool
        from google.protobuf.descriptor import FieldDescriptor

        super().__init__(manifest)
        self.descriptor_set = descriptor_set
        pool = descriptor_pool.DescriptorPool()
        files = list(descriptor_pb2.FileDescriptorSet.FromString(descriptor_set).file)
        if len(files) != 1 or files[0].dependency or files[0].syntax != "proto3":
            raise ValueError("telemetry descriptor must be self-contained")
        pool.Add(files[0])
        self.descriptor = pool.FindMessageTypeByName(self.manifest["root"])
        descriptors = {}
        for root_field in self.descriptor.fields:
            if root_field.type == FieldDescriptor.TYPE_MESSAGE:
                if root_field.is_repeated:
                    raise ValueError("repeated telemetry groups are unsupported")
                for field in root_field.message_type.fields:
                    descriptors[(root_field.number, field.number)] = (root_field, field)
            else:
                descriptors[(root_field.number,)] = (root_field,)
        if descriptors.keys() != self.fields.keys():
            raise ValueError("manifest fields do not match descriptor")
        self.descriptors = descriptors
        for path, field in self.fields.items():
            chain = descriptors[path]
            leaf = chain[-1]
            expected_type = SCALAR_TYPES.get(field["type"], FieldDescriptor.TYPE_ENUM)
            if leaf.type != expected_type or leaf.is_repeated != field["repeated"]:
                raise ValueError("manifest field type differs from descriptor")
            if [item.name for item in chain] != field["names"]:
                raise ValueError("manifest field names differ from descriptor")
            enum = {value.number: value.name for value in leaf.enum_type.values} if leaf.enum_type else {}
            if enum != {int(number): name for number, name in field.get("enum", {}).items()}:
                raise ValueError("manifest enum differs from descriptor")
            if leaf.type == FieldDescriptor.TYPE_MESSAGE or leaf.containing_oneof:
                raise ValueError("nested telemetry leaves and oneofs are unsupported")
            layout = field.get("layout", [])
            if (layout and not field["repeated"]) or len(set(layout)) != len(layout):
                raise ValueError("invalid array layout")
        for path, name in (((1,), "time"), ((2,), "packet_id")):
            field = self.fields.get(path, {})
            if field.get("names") != [name] or field.get("type") != "int64" or field.get("repeated"):
                raise ValueError("incompatible telemetry root")


def compare(producer, server):
    accepted, rejected = {}, {}
    if producer.manifest["root"] != server.manifest["root"]:
        raise ValueError("incompatible root message")
    source_groups = {path[0]: field["names"][0] for path, field in producer.fields.items() if len(path) == 2}
    target_groups = {path[0]: field["names"][0] for path, field in server.fields.items() if len(path) == 2}
    for path, source in producer.fields.items():
        target = server.fields.get(path)
        reason = ""
        if len(path) == 2 and path[0] in target_groups and source_groups[path[0]] != target_groups[path[0]]:
            reason = "protobuf group number was repurposed"
        elif target is None:
            reason = "unknown server field"
        elif any(source.get(key) != target.get(key) for key in ("names", "type", "repeated", "unit", "layout")):
            reason = "type, meaning, unit, or array layout differs"
        source_enum, target_enum = source.get("enum", {}), (target or {}).get("enum", {})
        common = source_enum.keys() & target_enum.keys()
        if not reason and any(source_enum[number] != target_enum[number] for number in common):
            reason = "enum number meaning differs"
        if not reason and source_enum and not common:
            reason = "no shared enum values"
        if reason:
            rejected[path] = reason
        else:
            accepted[path] = sorted(int(number) for number in common)
    if not ROOT_PATHS <= accepted.keys():
        raise ValueError("incompatible time/packet_id")
    if not accepted.keys() - ROOT_PATHS:
        raise ValueError("no compatible measurements")
    return accepted, rejected


def release_compatible(producer, server):
    try:
        _, rejected = compare(producer, server)
    except ValueError:
        return False
    return all(reason == "unknown server field" for reason in rejected.values())


def release_plan(metadata, producer, server):
    if metadata.get("projection_version") != 1 or metadata["schema_id"] != producer.schema_id:
        raise ValueError("release lacks numeric projection support")
    accepted, rejected = compare(producer, server)
    plan = {
        "format_version": 1, "producer_schema_id": producer.schema_id,
        "server": server.manifest, "metadata": metadata,
        "accepted": [{"path": list(path), "enum_values": accepted[path]} for path in sorted(accepted)],
        "excluded": [{"path": list(path), "reason": rejected[path]} for path in sorted(rejected)],
    }
    return dict(plan, plan_id=content_id(plan))
