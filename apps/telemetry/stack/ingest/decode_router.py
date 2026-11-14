"""Choose the decoder for each sensor_data record by its schema fingerprint.

The fingerprint is the `schema_id` of the wire contract the payload was
encoded with (`//lib/codegen/telemetry:artemis_schema_contract`). The bridge
stamps it as the `schema_fingerprint` Kafka header: its own deployed schema by
default, or the one a publisher named on `artemis/data/<schema_id>`.

A record is decoded only when its fingerprint is the deployed schema or an
approved entry in the decoder table (`apps/telemetry/schema_registry/
decoders.json`). Anything else is quarantined with a reason instead of being
decoded with definitions that might not match:

- `unknown_schema`: no entry for this fingerprint
- `unapproved_bundle`: the entry exists but is not approved
- `invalid_payload`: bad or contradictory headers, or bytes that do not decode

Records without the header predate fingerprints (or came from a bridge with no
packaged contract) and are decoded as the deployed schema, as before.

Today every approved Artemis schema uses the one compiled decoder; per-bundle
decoders come with versioned bundles. See section 10 of the telemetry design
sprint ("Runtime decode router").
"""

import json
import re

from google.protobuf.message import DecodeError
from stack.ingest.sensor_decode import car_from_headers, decode_sensor, decode_updates, payload_format

FINGERPRINT_HEADER = "schema_fingerprint"
SCHEMA_ID = re.compile(r"[a-f0-9]{64}\Z")
DECODERS = {
    "artemis_updates_v1": decode_updates,
    "snapshot_v1": lambda payload: [decode_sensor(payload, "Orion")],
}
ENTRY_FIELDS = {"schema_id", "car", "payload_format", "approved", "note"}


class Quarantined(Exception):
    def __init__(self, reason, detail, schema_id=None):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.schema_id = schema_id


def deployed_schema_id(contract_path):
    """The schema_id from a generated schema_contract.json."""
    with open(contract_path, "rb") as f:
        schema_id = json.load(f).get("schema_id")
    if not isinstance(schema_id, str) or not SCHEMA_ID.fullmatch(schema_id):
        raise ValueError(f"{contract_path} has no valid schema_id")
    return schema_id


class DecoderTable:
    def __init__(self, entries, deployed=None):
        self.deployed = deployed
        self.entries = {}
        for entry in entries:
            if not isinstance(entry, dict) or not {"schema_id", "car", "payload_format", "approved"} <= set(entry) \
                    or not set(entry) <= ENTRY_FIELDS:
                raise ValueError(f"invalid decoder table entry: {entry!r}")
            if not isinstance(entry["schema_id"], str) or not SCHEMA_ID.fullmatch(entry["schema_id"]):
                raise ValueError(f"invalid schema_id: {entry['schema_id']!r}")
            if entry["payload_format"] not in DECODERS or not isinstance(entry["approved"], bool):
                raise ValueError(f"invalid decoder table entry: {entry!r}")
            if entry["schema_id"] in self.entries:
                raise ValueError(f"duplicate schema_id {entry['schema_id']}")
            self.entries[entry["schema_id"]] = entry

    @classmethod
    def load(cls, path, deployed=None):
        with open(path, "rb") as f:
            table = json.load(f)
        if not isinstance(table, dict) or table.get("format_version") != 1 or not isinstance(table.get("decoders"), list):
            raise ValueError(f"{path} is not a format_version 1 decoder table")
        return cls(table["decoders"], deployed)

    def resolve(self, schema_id):
        """(entry, source) for a fingerprint, or (None, None) if unknown."""
        if schema_id == self.deployed:
            return {"car": "Artemis", "payload_format": "artemis_updates_v1", "approved": True}, "deployed"
        entry = self.entries.get(schema_id)
        return (entry, "table") if entry else (None, None)


def _single_header(headers, key):
    values = [value for name, value in headers or [] if name == key]
    if len(values) > 1:
        raise Quarantined("invalid_payload", f"more than one {key} header")
    if not values:
        return None
    value = values[0]
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def route(headers, payload, table):
    """Decode one sensor_data record or raise Quarantined.

    Returns {"car", "payload_format", "schema_id", "source", "updates"} where
    source is "deployed", "table" or "unlabelled" (no fingerprint header).
    """
    schema_id = _single_header(headers, FINGERPRINT_HEADER)
    try:
        car = car_from_headers(headers)
        fmt = payload_format(headers, car)
    except ValueError as error:
        raise Quarantined("invalid_payload", str(error), schema_id) from None

    if schema_id is None:
        source = "unlabelled"
    else:
        entry, source = table.resolve(schema_id)
        if entry is None:
            raise Quarantined("unknown_schema", "no decoder table entry", schema_id)
        if not entry["approved"]:
            raise Quarantined("unapproved_bundle", entry.get("note") or "entry is not approved", schema_id)
        if (entry["car"], entry["payload_format"]) != (car, fmt):
            raise Quarantined("invalid_payload",
                              f"headers say {car}/{fmt}, schema is {entry['car']}/{entry['payload_format']}", schema_id)

    try:
        updates = DECODERS[fmt](payload)
    except (DecodeError, ValueError) as error:
        raise Quarantined("invalid_payload", str(error) or type(error).__name__, schema_id) from None
    return {"car": car, "payload_format": fmt, "schema_id": schema_id, "source": source, "updates": updates}
