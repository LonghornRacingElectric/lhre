"""run.mcap: the whole run as JSON messages for Foxglove (or Lichtblick).

Log times are simulated time from 0 and nothing written depends on the wall
clock, so the same run writes byte-identical files.
Each topic has its own named JSON schema (Foxglove mixes up fields across
schemaless topics). Non-finite numbers are written as null, which plots as a
gap; Foxglove's parser rejects NaN. Topics:

  /driver, /rest_of_car            the scenario
  /vcu/status, /vcu/pedals, ...    the firmware's control snapshot, by group
  /vcu/inverter_command            the torque command as the inverter decodes it
  /can/vcu/<MESSAGE>               every frame the VCU sent, decoded by the spec
  /can/car/<MESSAGE>               every frame the rig sent it, decoded
  /can/raw                         frames the decode table has no entry for
  /model/outputs, /model/truth     the car model's outputs and internal truth
  /model/inputs                    what the car model was given
  /events                          foxglove.Log markers (events.py)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

from mcap.well_known import MessageEncoding, SchemaEncoding
from mcap.writer import Writer

from . import cosim
from .candecode import CanTable
from .events import Event
from .firmware import SATURATION_BITS
from .plant import OUTPUTS

NS_PER_MS = 1_000_000

# Snapshot field prefix -> topic. Fields without one go to /vcu/status.
SNAPSHOT_GROUPS = {
    'analog_': '/vcu/analog', 'pedals_': '/vcu/pedals', 'steering_': '/vcu/steering',
    'din_': '/vcu/digital_in', 'dout_': '/vcu/digital_out', 'torque_': '/vcu/torque',
    'battery_': '/vcu/battery', 'wheel_': '/vcu/wheel_control',
}
# Top-level snapshot fields whose names happen to start with a group prefix.
STATUS_FIELDS = {'torque_command_nm'}
FAULT_BITS = 22  # lib/spec/types.textproto VcuFaultBits
# Attachment media types by suffix; anything else is text/plain.
MEDIA_TYPES = {'.json': 'application/json', '.yml': 'application/yaml'}

# The foxglove.Log JSON schema (foxglove/schemas jsonschema/Log.json), trimmed
# to what the Log panel reads.
_LOG_SCHEMA = {
    'title': 'foxglove.Log', 'type': 'object',
    'properties': {
        'timestamp': {'type': 'object', 'properties': {
            'sec': {'type': 'integer'}, 'nsec': {'type': 'integer'}}},
        'level': {'type': 'integer'}, 'message': {'type': 'string'},
        'name': {'type': 'string'}, 'file': {'type': 'string'}, 'line': {'type': 'integer'},
    },
}


def _clean(value):
    """JSON-safe copy: non-finite floats become null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _schema_of(value) -> dict:
    if isinstance(value, bool):
        return {'type': 'boolean'}
    if isinstance(value, int):
        return {'type': 'integer'}
    if isinstance(value, (float, type(None))):
        return {'type': 'number'}
    if isinstance(value, str):
        return {'type': 'string'}
    if isinstance(value, dict):
        return {'type': 'object', 'properties': {k: _schema_of(v) for k, v in value.items()}}
    if isinstance(value, list):
        return {'type': 'array', 'items': _schema_of(value[0]) if value else {}}
    raise TypeError(f'no JSON schema for {type(value).__name__}')


class _Log:
    def __init__(self, writer: Writer):
        self.writer = writer
        self.channels: dict[str, int] = {}
        self.schemas: dict[str, dict] = {}
        self.sequence = 0

    def add(self, topic: str, t_ms: int, message: dict, schema_name: str | None = None,
            schema: dict | None = None) -> None:
        message = _clean(message)
        if topic not in self.channels:
            schema = schema or {'type': 'object',
                                'properties': {k: _schema_of(v) for k, v in message.items()}}
            name = schema_name or 'lhre.sil' + topic.replace('/', '.')
            schema_id = self.writer.register_schema(
                name, SchemaEncoding.JSONSchema,
                json.dumps({'title': name, **schema}, sort_keys=True).encode())
            self.channels[topic] = self.writer.register_channel(topic, MessageEncoding.JSON,
                                                                schema_id)
            self.schemas[topic] = schema
        t_ns = t_ms * NS_PER_MS
        self.writer.add_message(self.channels[topic], log_time=t_ns, publish_time=t_ns,
                                sequence=self.sequence,
                                data=json.dumps(message, allow_nan=False,
                                                separators=(',', ':')).encode())
        self.sequence += 1


def split_snapshot(snap: dict, state_name: Callable[[int], str],
                   fault_name: Callable[[int], str], transition_reason: Callable[[int], str],
                   enums: dict[str, dict[str, str]]) -> dict[str, dict]:
    """The snapshot by topic, with names added beside the codes."""
    topics: dict[str, dict] = {t: {} for t in ('/vcu/status', *SNAPSHOT_GROUPS.values())}
    for key, value in snap.items():
        for prefix, topic in SNAPSHOT_GROUPS.items():
            if key.startswith(prefix) and key not in STATUS_FIELDS:
                topics[topic][key[len(prefix):]] = value
                break
        else:
            topics['/vcu/status'][key] = value
    status = topics['/vcu/status']
    status['state_name'] = state_name(int(status['state']))
    status['last_transition'] = transition_reason(int(status['last_transition_id']))
    status['primary_fault_name'] = fault_name(int(status['primary_fault']))
    status['loop_state_name'] = enums.get('ShutdownLoopState', {}).get(
        str(status['loop_state']), '?')
    status['event_mode_name'] = enums.get('EventMode', {}).get(str(status['event_mode']), '?')
    status['fault_flags'] = {fault_name(i + 1): bool(int(status['faults']) >> i & 1)
                             for i in range(FAULT_BITS)}
    return topics


def write(path: Path, result: cosim.Result, events: list[Event],
          state_name: Callable[[int], str], fault_name: Callable[[int], str],
          transition_reason: Callable[[int], str], table: CanTable | None,
          metadata: dict[str, str], attachments: dict[str, bytes]) -> dict[str, object]:
    """Writes the run; returns topic -> message count."""
    enums = table.enums if table else {}
    with open(path, 'wb') as f:
        writer = Writer(f)
        writer.start(profile='', library='vcusil')
        log = _Log(writer)
        counts: dict[str, int] = {}

        def add(topic, t_ms, message, **kw):
            log.add(topic, t_ms, message, **kw)
            counts[topic] = counts.get(topic, 0) + 1

        for i, row in enumerate(result.rows):
            t = int(row['t_ms'])
            add('/driver', t, {'accelerator': row['accelerator'], 'brake_psi': row['brake_psi'],
                               'steering_deg': row['steering_deg'],
                               'rtd_pressed': bool(row['rtd_pressed'])})
            add('/rest_of_car', t, {'hvc_state': int(row['hvc_state']),
                                    'contactors_closed': bool(row['contactors_closed']),
                                    'shutdown_loop_v': row['shutdown_loop_v']})
            saturated = int(row['fw_saturated'])
            add('/vcu/inverter_command', t, {
                'enable': bool(row['fw_inverter_enable']),
                'torque_request_nm': row['fw_torque_request_nm'],
                'torque_limit_nm': row['fw_torque_limit_nm'],
                'frames': int(row['fw_torque_frames']),
                'saturated': saturated,
                'saturated_flags': {k: bool(saturated & m) for k, m in SATURATION_BITS.items()},
            })
            if i < len(result.snapshots) and result.snapshots[i]:
                snap = result.snapshots[i]
                tick = int(snap['now_ms'])
                for topic, message in split_snapshot(snap, state_name, fault_name,
                                                     transition_reason, enums).items():
                    add(topic, tick, message)
            outputs = {k: row[k] for k in OUTPUTS}
            outputs['dcPower'] = row['dcBusVoltage'] * row['dcBusCurrent']
            add('/model/outputs', t, outputs)
            if i < len(result.truth) and result.truth[i]:
                add('/model/truth', t, result.truth[i])
            add('/model/inputs', t, {'motorTorqueCommand': row['u_motor_torque_nm'],
                                     'regenTorqueLimitCommand': row['u_regen_limit_nm'],
                                     'brakePedalCommand': row['u_brake'],
                                     'steeringAngleCommand': row['u_steering_rad']})
        for t_ms, bus, frame_id, from_vcu, data in result.frames:
            decoded = table.decode(bus, frame_id, data) if table else None
            if decoded is None:
                add('/can/raw', t_ms, {'bus': bus, 'id': frame_id, 'from_vcu': from_vcu,
                                       'data': list(data)})
            else:
                side = 'vcu' if from_vcu else 'car'
                add(f'/can/{side}/{decoded.message}', t_ms, decoded.signals)
        for e in events:
            add('/events', e.t_ms, {
                'timestamp': {'sec': e.t_ms // 1000, 'nsec': e.t_ms % 1000 * NS_PER_MS},
                'level': e.level, 'message': e.message, 'name': e.name, 'file': '', 'line': 0},
                schema_name='foxglove.Log', schema=_LOG_SCHEMA)
        writer.add_metadata('vcusil', {k: str(v) for k, v in metadata.items()})
        for name, data in attachments.items():
            writer.add_attachment(create_time=0, log_time=0, name=name,
                                  media_type=MEDIA_TYPES.get(Path(name).suffix, 'text/plain'),
                                  data=data)
        writer.finish()
    return {'topics': len(counts), 'messages': sum(counts.values()), 'per_topic': counts}
