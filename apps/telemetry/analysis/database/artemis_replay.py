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
frames stop arriving. `Fault` holds a `diagnostics_high` fault active for a
window, the input events_faults turns into raised/cleared events. Both can
repeat, and `start_at` begins the replay partway through the drive.
`paho_testing.py --replay` (`//apps/telemetry/analysis:publish_demo`) is the
command-line front end.
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


class Window:
    """When an injected change is active, in seconds of replay.

    Active from `after_s` for `for_s` seconds (forever if None). With
    `every_s`, the same window comes back every `every_s` seconds, so a demo
    shows it again without restarting the replay.
    """

    def __init__(self, after_s, for_s=None, every_s=None):
        if not math.isfinite(after_s) or after_s < 0:
            raise ValueError("start must be a finite, non-negative number of seconds")
        if for_s is not None and (not math.isfinite(for_s) or for_s <= 0):
            raise ValueError("length must be a finite, positive number of seconds")
        if every_s is not None:
            if for_s is None:
                raise ValueError("a repeating window needs a length")
            if not math.isfinite(every_s) or every_s < after_s + for_s:
                raise ValueError(f"repeat period {every_s:g} s must fit the window ({after_s:g} s + {for_s:g} s)")
        self.start_s = after_s
        self.end_s = math.inf if for_s is None else after_s + for_s
        self.every_s = every_s

    def offset(self, elapsed_s):
        """Seconds since the window opened, or None while it is closed."""
        t = elapsed_s % self.every_s if self.every_s else elapsed_s
        return t - self.start_s if self.start_s <= t < self.end_s else None


def _mark_updated(values, group, field, updated_fields):
    path = (values.DESCRIPTOR.fields_by_name[group].number,
            getattr(values, group).DESCRIPTOR.fields_by_name[field].number)
    if path not in updated_fields:
        updated_fields.append(path)


class Dropout(Window):
    """One board goes quiet: its `<board>_last_seen_s` keeps climbing."""

    def __init__(self, board, after_s, for_s=None, every_s=None):
        if board not in BOARDS:
            raise ValueError(f"unknown board {board!r}; expected one of {', '.join(BOARDS)}")
        super().__init__(after_s, for_s, every_s)
        self.board = board
        self.field = board + AGE_SUFFIX

    def apply(self, values, updated_fields, elapsed_s):
        quiet_s = self.offset(elapsed_s)
        if quiet_s is None:
            return
        status = values.board_status
        status.SetInParent()
        setattr(status, self.field, max(getattr(status, self.field), 0.0) + quiet_s)
        _mark_updated(values, "board_status", self.field, updated_fields)


FAULT_GROUP = "diagnostics_high"
FAULT_MASKS = ("post_faults", "run_faults")  # inverter fault words; bits 0-23 survive float32
MASK_BITS = 24


def fault_names():
    """Every fault --fault accepts: diagnostics_high flags and inverter mask bits."""
    group = artemis_pb2.ArtemisSensorData.DESCRIPTOR.fields_by_name[FAULT_GROUP].message_type
    flags = [f.name for f in group.fields if f.type == FieldDescriptor.TYPE_BOOL]
    return flags + [f"{mask}[{bit}]" for mask in FAULT_MASKS for bit in range(MASK_BITS)]


class Fault(Window):
    """Holds one diagnostics_high fault active for the window, then clears it.

    `name` is a flag such as `apps_mismatch` or a mask bit such as
    `run_faults[2]`. While active the fault is set on every frame. The field is
    also sent outside the window, with the recorded value, so the clear reaches
    events_faults even if a recording does not report diagnostics every frame:
    a field missing from an update means "not reported", not "cleared".
    """

    def __init__(self, name, after_s, for_s=None, every_s=None):
        if name not in fault_names():
            raise ValueError(f"unknown fault {name!r}; expected a {FAULT_GROUP} flag or e.g. run_faults[2]")
        super().__init__(after_s, for_s, every_s)
        self.name = name
        self.field, _, bit = name.partition("[")
        self.bit = int(bit.rstrip("]")) if bit else None

    def apply(self, values, updated_fields, elapsed_s):
        group = getattr(values, FAULT_GROUP)
        group.SetInParent()
        if self.offset(elapsed_s) is not None:
            if self.bit is None:
                setattr(group, self.field, True)
            else:
                setattr(group, self.field, float(int(getattr(group, self.field)) | (1 << self.bit)))
        _mark_updated(values, FAULT_GROUP, self.field, updated_fields)


def start_at(recording, start_s):
    """The recording rotated to begin `start_s` seconds in, wrapping the skipped part to the end.

    Lets a demo reach an interesting stretch of the drive (the UTA run's own
    STOMP blip is 72.6 s in) without waiting for it.
    """
    total_s = sum(max(gap_s, 0.0) for gap_s, _ in recording)
    if not math.isfinite(start_s) or not 0 <= start_s < total_s:
        raise ValueError(f"start must be between 0 and {total_s:.1f} s, the recording's length")
    elapsed_s = 0.0
    for i, (gap_s, _) in enumerate(recording):
        elapsed_s += max(gap_s, 0.0)
        if elapsed_s > start_s:
            return recording[i:] + recording[:i]
    return list(recording)


def replay(recording, publish, dropout=None, faults=(), loop=True, should_stop=lambda: False,
           sleep=time.sleep, now_ns=time.time_ns):
    """Publishes the recording at its recorded pace, restamped to now.

    `publish(payload)` receives TelemetryBatch bytes. Returns the number of
    frames sent. Windows for the dropout and faults are measured in seconds
    since the replay started, so they line up with what the data shows.
    """
    sent, elapsed_s = 0, 0.0
    changes = ([dropout] if dropout is not None else []) + list(faults)
    while True:
        for gap_s, frame in recording:
            if should_stop():
                return sent
            gap_s = max(gap_s, 0.0)
            sleep(gap_s)
            elapsed_s += gap_s
            values, updated = to_artemis(frame)
            sent += 1
            for change in changes:
                change.apply(values, updated, elapsed_s)
            publish(batch_payload(values, updated, now_ns()))
        if not loop:
            return sent
