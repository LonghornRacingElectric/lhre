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
