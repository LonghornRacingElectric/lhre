"""Decode the raw Kafka/MQTT wire format without starting ingest workers."""
from google.protobuf.json_format import MessageToDict
from stack.ingest.protobuf import can_packets_pb2, artemis_pb2

CAR_MESSAGES = {
    "Artemis": artemis_pb2.ArtemisSensorData,
    "Orion": can_packets_pb2.OrionSensorData,
}
SENSOR_TABLES = (
    "packet", "dynamics", "controls", "pack", "diagnostics", "diagnostics_high",
    "diagnostics_low", "thermal", "board_status",
)


def car_from_headers(headers):
    values = [value.decode("utf-8") for key, value in headers or [] if key == "car_type"]
    if len(values) != 1 or values[0] not in CAR_MESSAGES:
        raise ValueError("sensor_data requires exactly one known car_type header")
    return values[0]


def decode_sensor(payload, car):
    if car != "Orion":
        raise ValueError("Artemis uses explicit native update batches")
    row = CAR_MESSAGES[car]()
    row.ParseFromString(payload)
    if row.time <= 0 or (car == "Orion" and row.packet_id <= 0):
        raise ValueError("sensor packet requires positive BEVO time (and packet_id for Orion)")
    result = MessageToDict(row, preserving_proto_field_name=True,
                           always_print_fields_with_no_presence=True, use_integers_for_enums=True)
    if car in CAR_MESSAGES:
        dynamics = result.get("dynamics", {})
        for corner in ("bl", "br", "fl", "fr"):
            source = f"{corner}w_speed"
            if source in dynamics:
                dynamics[f"{corner}_wheel_speed"] = dynamics.pop(source)
    return result


def sensor_rows(message, models):
    """Only sensor tables may be written by a wire message."""
    rows = {}
    for table in SENSOR_TABLES:
        model = models.get(table)
        if model is None:
            continue
        source = message if table == "packet" else message.get(table)
        if source is None:
            continue
        rows[table] = {column.name: source[column.name] for column in model.__table__.columns
                       if column.name in source}
        rows[table]["packet_id"] = int(message["packet_id"])
    return rows

NATIVE_FORMAT = "artemis_updates_v1"


def payload_format(headers, car):
    values = [v.decode("utf-8") for k, v in headers or [] if k == "payload_format"]
    expected = NATIVE_FORMAT if car == "Artemis" else "snapshot_v1"
    if values != [expected] and not (car == "Orion" and not values):
        raise ValueError(f"{car} requires payload_format={expected}")
    return expected


def decode_updates(payload):
    """Presence is explicit: protobuf scalar defaults still represent fresh data."""
    batch = artemis_pb2.TelemetryBatch.FromString(payload)
    if batch.format_version != 1 or not batch.updates or len(batch.updates) > 64:
        raise ValueError("Invalid Artemis update batch")
    result = []
    for update in batch.updates:
        stamp = update.sample_time_ns
        root = update.values
        if stamp <= 0 or root.time != stamp // 1_000_000 or not update.updated_fields:
            raise ValueError("Invalid Artemis receipt timestamp or empty update")
        data = MessageToDict(root, preserving_proto_field_name=True,
                             always_print_fields_with_no_presence=True, use_integers_for_enums=True)
        row = {"time": int(root.time), "sample_time_ns": stamp}
        seen = set()
        for path in update.updated_fields:
            key = (path.group, path.field)
            group = root.DESCRIPTOR.fields_by_number.get(path.group)
            field = group.message_type.fields_by_number.get(path.field) if group and group.message_type else None
            if not field or key in seen or not root.HasField(group.name):
                raise ValueError("Unknown, duplicate or missing Artemis update field")
            seen.add(key)
            name = field.name
            if group.name == "dynamics" and name in ("blw_speed", "brw_speed", "flw_speed", "frw_speed"):
                name = name[:2] + "_wheel_speed"
            row.setdefault(group.name, {})[name] = data[group.name][field.name]
        result.append(row)
    return result


class LatestState:
    """The live dashboard cache is separate from fresh historical observations."""
    def __init__(self):
        self.values = {}
        self.times = {}

    def apply(self, updates):
        for update in updates:
            stamp = update["sample_time_ns"]
            self.values["time"] = max(self.values.get("time", 0), update["time"])
            for group, fields in update.items():
                if not isinstance(fields, dict):
                    continue
                for name, value in fields.items():
                    key = (group, name)
                    if stamp >= self.times.get(key, 0):
                        self.values.setdefault(group, {})[name] = value
                        self.times[key] = stamp
        return self.values
