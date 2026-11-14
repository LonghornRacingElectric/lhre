"""Publish generated Artemis pools at independent rates through MQTT."""
from collections import Counter
from functools import lru_cache
import heapq
import json
import math
import os
import time

from google.protobuf.descriptor import FieldDescriptor
from paho.mqtt import client as mqtt
from runfiles import Runfiles
from stack.ingest.protobuf import artemis_pb2


def contract(path=None):
    path = path or os.getenv("ARTEMIS_STORAGE_CONTRACT") or Runfiles.Create().Rlocation("_main/lib/codegen/telemetry/storage_contract.json")
    with open(path) as source:
        return json.load(source)


@lru_cache(maxsize=1)
def field_definitions():
    path = os.getenv("ARTEMIS_SCHEMA_CONTRACT") or Runfiles.Create().Rlocation("_main/lib/codegen/telemetry/artemis_schema_contract.json")
    with open(path) as source:
        return {".".join(field["names"]): field for field in json.load(source)["fields"]}


def sample_value(field, elapsed, seed, index=None):
    wave = (1 + math.sin(elapsed * .7 + seed * .01)) / 2
    values = {
        "apps1_travel": wave * 100, "apps2_travel": wave * 100,
        "commanded_torque": wave * 120, "torque_request": wave * 120,
        "hv_pack_v": 410 + wave * 10, "hv_c": -20 + wave * 140,
        "hv_soc": 80 - elapsed * .005, "ambient_temp": 25 + wave * 3,
        "inverter_temp": 40 + wave * 15, "motor_temp": 45 + wave * 15,
        "gps_speed": 30 + wave * 10, "steer_col_angle": (wave - .5) * 60,
    }
    if index is not None:
        if field.name in ("gps", "gps_precise"):
            values = [30.289464 + math.cos(elapsed * .08) * .0005, -97.735303 + math.sin(elapsed * .08) * .0008, 250., .1, 1., elapsed]
            return values[index]
        if field.name == "cells_v":
            return 3.6 + wave * .3 + index * .0001
        value = wave * 10 + index * .01
        if field.type == FieldDescriptor.TYPE_BOOL:
            return bool(index % 2)
        return float(value) if field.type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE) else int(value)
    if field.type == FieldDescriptor.TYPE_BOOL:
        return int(elapsed) % 8 < 6
    if field.type == FieldDescriptor.TYPE_ENUM:
        return field.enum_type.values[0].number
    if field.type == FieldDescriptor.TYPE_STRING:
        return "demo"
    if field.type == FieldDescriptor.TYPE_BYTES:
        return b"demo"
    value = values.get(field.name, wave * (30 if "wheel_speed" in field.name else 10))
    return float(value) if field.type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE) else int(value)


def update_for(pool, sample_ns, elapsed, seed):
    update = artemis_pb2.TelemetryUpdate(sample_time_ns=sample_ns)
    update.values.time = sample_ns // 1_000_000
    for definition in pool["fields"]:
        group_id, field_id = definition["path"]
        group = update.values.DESCRIPTOR.fields_by_number[group_id]
        field = group.message_type.fields_by_number[field_id]
        target = getattr(update.values, group.name)
        if field.is_repeated:
            length = len(field_definitions()[f"{group.name}.{field.name}"]["layout"])
            getattr(target, field.name).extend(sample_value(field, elapsed, seed, index) for index in range(length))
        else:
            setattr(target, field.name, sample_value(field, elapsed, seed))
        update.updated_fields.add(group=group_id, field=field_id)
    return update


def publish(duration=60, rate_scale=1, seed=42, host=None, port=None, storage=None, update_factory=update_for):
    if duration <= 0 or rate_scale <= 0:
        raise ValueError("duration and rate_scale must be positive")
    pools = (storage or contract())["pools"]
    rates = [(pool["frequency_hz"] or (10 if pool["group"] == "dynamics" else 1)) * rate_scale for pool in pools]
    schedule = [(0., index, 0) for index in range(len(pools))]
    heapq.heapify(schedule)
    client = mqtt.Client(client_id="artemis-demo-" + str(os.getpid()))
    client.connect(host or os.getenv("MQTT_HOST", "localhost"), int(port or os.getenv("MQTT_PORT", "1883")))
    client.loop_start()
    start_ns, started = time.time_ns(), time.monotonic()
    counts = Counter()
    batch = artemis_pb2.TelemetryBatch(format_version=1)
    flushed = started

    def flush():
        if batch.updates:
            result = client.publish("artemis/data", batch.SerializeToString(), qos=1)
            result.wait_for_publish(timeout=5)
            if not result.is_published():
                raise TimeoutError("MQTT publish did not complete")
            batch.ClearField("updates")

    try:
        while schedule[0][0] < duration:
            due, index, sample = heapq.heappop(schedule)
            remaining = started + due - time.monotonic()
            if remaining > 0:
                flush()
                time.sleep(remaining)
            batch.updates.append(update_factory(pools[index], start_ns + round(due * 1e9), due, seed))
            counts[pools[index]["table"]] += 1
            sample += 1
            heapq.heappush(schedule, (sample / rates[index], index, sample))
            if len(batch.updates) == 64 or time.monotonic() - flushed >= .005:
                flush()
                flushed = time.monotonic()
        flush()
    finally:
        client.disconnect()
        client.loop_stop()
    return {"start_ms": start_ns // 1_000_000, "end_ms": (start_ns + round(duration * 1e9)) // 1_000_000 + 1, "pool_samples": dict(counts), "duration": duration, "rate_scale": rate_scale, "seed": seed}
