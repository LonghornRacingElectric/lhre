"""Canonical content identity for per-car signal catalogs."""

import hashlib
import json
import math
import re

CAR = re.compile(r"[a-z][a-z0-9_-]*\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
SIGNAL = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z")
FIELDS = {"signal", "group", "value_type", "repeated", "module", "unit", "rate_hz", "channel_type"}


def _normalize(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("catalog numbers must be finite")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def content(car, signals):
    if not isinstance(car, str) or not CAR.fullmatch(car):
        raise ValueError("car must be a lowercase slug")
    rows = [{key: value for key, value in row.items() if key != "schema_version"} for row in signals]
    names = set()
    for row in rows:
        if set(row) != FIELDS or not isinstance(row["signal"], str) or not SIGNAL.fullmatch(row["signal"]):
            raise ValueError("invalid signal fields")
        if row["signal"] in names:
            raise ValueError("duplicate signal")
        names.add(row["signal"])
        if not all(isinstance(row[key], str) for key in ("group", "value_type")):
            raise ValueError("invalid signal type or group")
        if not isinstance(row["repeated"], bool):
            raise ValueError("repeated must be boolean")
        if any(row[key] is not None and not isinstance(row[key], str) for key in ("module", "unit")):
            raise ValueError("invalid module or unit")
        rate = row["rate_hz"]
        if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
            raise ValueError("rate_hz must be finite and nonnegative")
        if row["channel_type"] not in ("telemetry", "event", "health"):
            raise ValueError("invalid channel type")
    return _normalize({"car": car, "signals": sorted(rows, key=lambda row: row["signal"])})


def version_for(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build(car, signals):
    payload = content(car, signals)
    version = version_for(payload)
    return {"car": car, "schema_version": version,
            "signals": payload["signals"]}


def validate(catalog):
    if not isinstance(catalog, dict) or set(catalog) != {"car", "schema_version", "signals"}:
        raise ValueError("invalid catalog envelope")
    version = catalog["schema_version"]
    if not isinstance(version, str) or not HASH.fullmatch(version) or not isinstance(catalog["signals"], list):
        raise ValueError("invalid catalog version or signals")
    for row in catalog["signals"]:
        if not isinstance(row, dict) or "schema_version" in row:
            raise ValueError("schema_version belongs only at catalog level")
    expected = build(catalog["car"], catalog["signals"])
    if expected["schema_version"] != version:
        raise ValueError("catalog content does not match schema_version")
    return expected
