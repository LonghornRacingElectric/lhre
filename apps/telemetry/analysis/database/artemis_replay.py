"""Replay a recorded drive as Artemis telemetry, the way the car sends it.

The car publishes `TelemetryBatch` messages to MQTT `artemis/data`. Ingest
forwards them to Kafka unchanged, and every processor decodes them with
`stack.ingest.sensor_decode.decode_updates`. This module turns a recording into
those same bytes, so a replay exercises the whole pipeline.

Recordings are gzip files of uint32-LE length-prefixed `OrionSensorData`
frames (the format `stack/simulate` reads). Fields are copied into
`ArtemisSensorData` by name, so the replay keeps working if the two schemas'
field numbers drift apart.

`Dropout` makes one board look like it stopped talking for a set window: its
`<board>_last_seen_s` keeps climbing, just as BEVO reports it when a board's CAN
frames stop arriving.
"""

import gzip
import math
import struct
import time

from google.protobuf.descriptor import FieldDescriptor
from stack.ingest.protobuf import artemis_pb2, can_packets_pb2

FORMAT_VERSION = 1
AGE_SUFFIX = "_last_seen_s"
BOARDS = tuple(
    field.name.removesuffix(AGE_SUFFIX)
    for field in artemis_pb2.ArtemisSensorData.DESCRIPTOR.fields_by_name["board_status"].message_type.fields
    if field.name.endswith(AGE_SUFFIX)
)


def load_recording(path):
    """Returns [(seconds since the previous frame, frame)] in recorded order.

    The first frame's gap is the mean gap, so looping neither stalls nor bursts.
    """
    with gzip.open(path, "rb") as f:
        blob = f.read()
    frames, i = [], 0
    while i < len(blob):
        (n,) = struct.unpack_from("<I", blob, i)
        frames.append(can_packets_pb2.OrionSensorData.FromString(blob[i + 4:i + 4 + n]))
        i += 4 + n
    if not frames:
        raise ValueError(f"no frames in {path}")
    gaps = [(b.time - a.time) / 1000.0 for a, b in zip(frames, frames[1:])]
    seam = sum(gaps) / len(gaps) if gaps else 0.05
    return list(zip([seam] + gaps, frames))


def _is_repeated(field):
    if hasattr(field, "is_repeated"):
        return bool(field.is_repeated)
    return field.label == FieldDescriptor.LABEL_REPEATED


def _source_names(name):
    # Artemis spells wheel speeds `flw_speed`; Orion spells them `fl_wheel_speed`.
    if name.endswith("w_speed") and len(name) == len("flw_speed"):
        return (name, name[:2] + "_wheel_speed")
    return (name,)


def to_artemis(frame):
    """Copies a recorded frame into ArtemisSensorData.

    Returns (values, updated_fields) where updated_fields lists the
    (group number, field number) of every field copied. Each one counts as a
    fresh observation, the same as a full CAN refresh on the car.
    """
    values = artemis_pb2.ArtemisSensorData()
    updated = []
    for group in values.DESCRIPTOR.fields:
        if group.message_type is None or group.name not in frame.DESCRIPTOR.fields_by_name:
            continue
        if not frame.HasField(group.name):
            continue
        source, target = getattr(frame, group.name), getattr(values, group.name)
        copied = 0
        for field in group.message_type.fields:
            if field.message_type is not None:
                continue
            name = next((n for n in _source_names(field.name) if n in source.DESCRIPTOR.fields_by_name), None)
            if name is None:
                continue
            value = getattr(source, name)
            try:
                if _is_repeated(field):
                    getattr(target, field.name).extend(value)
                else:
                    setattr(target, field.name, value)
            except (AttributeError, TypeError, ValueError):
                continue  # same name, different type: leave it out
            updated.append((group.number, field.number))
            copied += 1
        if copied:
            target.SetInParent()
    return values, updated


def present_fields(values):
    """Every scalar field of every group set on `values`, as (group, field) numbers."""
    return [
        (group.number, field.number)
        for group in values.DESCRIPTOR.fields
        if group.message_type is not None and values.HasField(group.name)
        for field in group.message_type.fields
        if field.message_type is None
    ]


def batch_payload(values, updated_fields, sample_time_ns):
    """Wraps one sample in the TelemetryBatch the car puts on `artemis/data`."""
    values.time = sample_time_ns // 1_000_000
    batch = artemis_pb2.TelemetryBatch(format_version=FORMAT_VERSION)
    update = batch.updates.add(sample_time_ns=sample_time_ns)
    update.values.CopyFrom(values)
    for group, field in updated_fields:
        update.updated_fields.add(group=group, field=field)
    return batch.SerializeToString()


class Dropout:
    """One board goes quiet from `after_s` for `for_s` seconds (forever if None)."""

    def __init__(self, board, after_s, for_s=None):
        if board not in BOARDS:
            raise ValueError(f"unknown board {board!r}; expected one of {', '.join(BOARDS)}")
        if not math.isfinite(after_s) or after_s < 0:
            raise ValueError("dropout start must be a finite, non-negative number of seconds")
        if for_s is not None and (not math.isfinite(for_s) or for_s <= 0):
            raise ValueError("dropout length must be a finite, positive number of seconds")
        self.board = board
        self.field = board + AGE_SUFFIX
        self.start_s = after_s
        self.end_s = math.inf if for_s is None else after_s + for_s

    def apply(self, values, updated_fields, elapsed_s):
        if not self.start_s <= elapsed_s < self.end_s:
            return
        status = values.board_status
        status.SetInParent()
        recorded = getattr(status, self.field)
        setattr(status, self.field, max(recorded, 0.0) + (elapsed_s - self.start_s))
        path = (values.DESCRIPTOR.fields_by_name["board_status"].number,
                status.DESCRIPTOR.fields_by_name[self.field].number)
        if path not in updated_fields:
            updated_fields.append(path)


def replay(recording, publish, dropout=None, loop=True, should_stop=lambda: False,
           sleep=time.sleep, now_ns=time.time_ns):
    """Publishes the recording at its recorded pace, restamped to now.

    `publish(payload)` receives TelemetryBatch bytes. Returns the number of
    frames sent. Elapsed time for the dropout window is measured along the
    recording, so it lines up with what the data shows.
    """
    sent, elapsed_s = 0, 0.0
    while True:
        for gap_s, frame in recording:
            if should_stop():
                return sent
            gap_s = max(gap_s, 0.0)
            sleep(gap_s)
            elapsed_s += gap_s
            values, updated = to_artemis(frame)
            sent += 1
            if dropout is not None:
                dropout.apply(values, updated, elapsed_s)
            publish(batch_payload(values, updated, now_ns()))
        if not loop:
            return sent
