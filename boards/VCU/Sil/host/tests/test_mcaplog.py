"""run.mcap: complete, exact and deterministic."""

import json
import math

import pytest
from fakes import Launch, ToyCar
from mcap.reader import make_reader

from vcusil import cosim, events, mcaplog
from vcusil.candecode import CanTable
from vcusil.firmware import Firmware

@pytest.fixture(scope='module')
def run(lib, can_table_path, tmp_path_factory):
    table = CanTable(can_table_path)
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), fw, ToyCar())
        marks = events.derive(result, fw.state_name, fw.fault_name, fw.transition_reason)
        names = fw.state_name, fw.fault_name, fw.transition_reason
    out = tmp_path_factory.mktemp('mcap')

    def write(name):
        path = out / name
        stats = mcaplog.write(path, result, marks, *names, table,
                              metadata={'scenario': 'launch'},
                              attachments={'meta.json': b'{}'})
        return path, stats
    path, stats = write('run.mcap')
    return result, marks, path, stats, write


def read_all(path):
    with open(path, 'rb') as f:
        reader = make_reader(f)
        messages = {}
        schemas = {}
        for schema, channel, message in reader.iter_messages():
            messages.setdefault(channel.topic, []).append(
                (message.log_time, json.loads(message.data)))
            schemas[channel.topic] = (schema.name, json.loads(schema.data))
        f.seek(0)
        reader = make_reader(f)
        attachments = {a.name: a.data for a in reader.iter_attachments()}
        metadata = {m.name: m.metadata for m in reader.iter_metadata()}
    return messages, schemas, attachments, metadata


def test_every_step_reaches_its_topic_exactly(run):
    result, _, path, stats, _ = run
    messages, schemas, attachments, metadata = read_all(path)
    steps = len(result.rows)
    for topic in ('/driver', '/rest_of_car', '/vcu/inverter_command', '/vcu/status',
                  '/vcu/pedals', '/vcu/analog', '/vcu/torque', '/model/outputs',
                  '/model/truth', '/model/inputs'):
        assert len(messages[topic]) == steps, topic
    for (t_ns, msg), row in zip(messages['/model/outputs'], result.rows, strict=True):
        assert t_ns == row['t_ms'] * 1_000_000
        assert msg['rearU'] == row['rearU'] and msg['dcBusCurrent'] == row['dcBusCurrent']
    for (t_ns, msg), snap in zip(messages['/vcu/status'], result.snapshots, strict=True):
        assert t_ns == snap['now_ms'] * 1_000_000  # stamped at the control tick
        assert msg['torque_command_nm'] == snap['torque_command_nm']
        assert msg['fault_flags']['SHUTDOWN_OPEN'] == bool(snap['faults'] >> 8 & 1)
    for (_, msg), snap in zip(messages['/vcu/pedals'], result.snapshots, strict=True):
        assert msg['pedal_travel'] == snap['pedals_pedal_travel']
    for (_, msg), snap in zip(messages['/vcu/torque'], result.snapshots, strict=True):
        assert set(msg) == {'lookup', 'derated', 'power_limited', 'traction', 'power_w',
                            'power_trim_w'}
        assert msg['power_w'] == snap['torque_power_w']
    assert messages['/model/truth'][0][1] == {'velX': 0.0, 'kappa_RL': 0.0, 'sideslip': None}
    assert schemas['/model/truth'][1]['properties']['sideslip'] == {'type': 'number'}
    assert stats['messages'] == sum(len(v) for v in messages.values())
    assert attachments == {'meta.json': b'{}'}
    assert metadata == {'vcusil': {'scenario': 'launch'}}


def test_each_topic_has_its_own_named_schema(run):
    _, _, path, _, _ = run
    _, schemas, _, _ = read_all(path)
    names = [name for name, _ in schemas.values() if name != 'foxglove.Log']
    assert len(names) == len(set(names))
    assert schemas['/events'][0] == 'foxglove.Log'
    assert schemas['/vcu/status'][1]['properties']['state_name'] == {'type': 'string'}
    assert schemas['/vcu/status'][1]['properties']['fault_flags']['type'] == 'object'


def test_frames_are_decoded_by_the_spec(run):
    result, _, path, _, _ = run
    messages, _, _, _ = read_all(path)
    commands = messages['/can/vcu/INVERTER_TORQUE_COMMAND']
    assert len(commands) == len(result.rows)
    by_t = {r['t_ms']: r for r in result.rows}
    for t_ns, msg in commands:
        row = by_t[t_ns // 1_000_000]
        # The C++ accessor rounds to float32; the decoder keeps float64.
        assert msg['torque_request'] == pytest.approx(row['fw_torque_request_nm'], abs=1e-4)
        assert msg['enable'] == bool(row['fw_inverter_enable'])
    faults = messages['/can/vcu/VCU_FAULTS']
    assert {m['state_name'] for _, m in faults} >= {'IDLE', 'PRECHARGE', 'DRIVE'}
    speeds = messages['/can/car/INVERTER_SPEED']
    assert all(isinstance(m['motor_speed'], int) for _, m in speeds)
    assert '/can/raw' not in messages
    total = sum(len(v) for k, v in messages.items() if k.startswith('/can/'))
    assert total == len(result.frames)


def test_events_use_foxglove_log(run):
    _, marks, path, _, _ = run
    messages, _, _, _ = read_all(path)
    logs = messages['/events']
    assert [m['message'] for _, m in logs] == [e.message for e in marks]
    t_ns, first = logs[1]
    assert first['timestamp'] == {'sec': t_ns // 10**9, 'nsec': t_ns % 10**9}
    assert set(first) == {'timestamp', 'level', 'message', 'name', 'file', 'line'}


def test_the_file_is_byte_identical_when_written_again(run):
    _, _, path, _, write = run
    again, _ = write('again.mcap')
    assert again.read_bytes() == path.read_bytes()


def test_non_finite_values_become_null():
    assert mcaplog._clean({'a': math.nan, 'b': [math.inf, 1.0], 'c': {'d': -math.inf}}) == {
        'a': None, 'b': [None, 1.0], 'c': {'d': None}}


def test_attachments_get_their_media_type(tmp_path):
    path = tmp_path / 'run.mcap'
    mcaplog.write(path, cosim.Result(scenario='x'), [], str, str, str, None, metadata={},
                  attachments={'meta.json': b'{}', 'scenario.yml': b'schema: 1\n',
                               'summary.txt': b'ok\n'})
    with open(path, 'rb') as f:
        types = {a.name: (a.media_type, a.data, a.log_time, a.create_time)
                 for a in make_reader(f).iter_attachments()}
    assert types == {'meta.json': ('application/json', b'{}', 0, 0),
                     'scenario.yml': ('application/yaml', b'schema: 1\n', 0, 0),
                     'summary.txt': ('text/plain', b'ok\n', 0, 0)}
