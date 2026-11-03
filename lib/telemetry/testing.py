"""Small versioned descriptor fixtures, independent of the current car schema."""

from google.protobuf import descriptor_pb2

from lib.telemetry.contract import Schema, seal_manifest


def fixture_schema(value_type="float", unit="V", layout=("left", "right"), extra=False, enum=None):
    enum = enum or {0: "OFF", 1: "ON"}
    file = descriptor_pb2.FileDescriptorProto(name="fixture.proto", package="orion", syntax="proto3")
    mode = file.enum_type.add(name="Mode")
    for number, name in sorted(enum.items()):
        mode.value.add(name=name, number=number)
    root = file.message_type.add(name="OrionSensorData")
    root.field.add(name="time", number=1, type=3)
    root.field.add(name="packet_id", number=2, type=3)
    root.field.add(name="controls", number=3, type=11, type_name=".orion.Controls")
    group = file.message_type.add(name="Controls")
    group.field.add(name="voltage", number=1, type={"float": 2, "string": 9, "double": 1}[value_type])
    group.field.add(name="enabled", number=2, type=8)
    group.field.add(name="samples", number=3, type=2, label=3)
    group.field.add(name="mode", number=4, type=14, type_name=".orion.Mode")
    fields = []
    for path, names, scalar, repeated, field_unit, elements, values in (
        ([1], ["time"], "int64", False, "ms", [], {}),
        ([2], ["packet_id"], "int64", False, "", [], {}),
        ([3, 1], ["controls", "voltage"], value_type, False, unit, [], {}),
        ([3, 2], ["controls", "enabled"], "bool", False, "", [], {}),
        ([3, 3], ["controls", "samples"], "float", True, "", list(layout), {}),
        ([3, 4], ["controls", "mode"], "Mode", False, "", [], {str(number): name for number, name in enum.items()}),
    ):
        fields.append(dict(path=path, names=names, type=scalar, repeated=repeated, unit=field_unit, layout=elements, enum=values))
    if extra:
        group.field.add(name="new_value", number=5, type=2)
        fields.append(dict(path=[3, 5], names=["controls", "new_value"], type="float", repeated=False, unit="", layout=[], enum={}))
    manifest = seal_manifest(dict(format_version=1, root="orion.OrionSensorData", fields=fields))
    descriptors = descriptor_pb2.FileDescriptorSet()
    descriptors.file.add().CopyFrom(file)
    return Schema(manifest, descriptors.SerializeToString())
