import dataclasses
import json

import pytest
from fakes import Launch, ToyCar

from vcusil import cosim
from vcusil.candecode import CanTable
from vcusil.firmware import Firmware

# A two-message table in the generator's format, covering every signal kind.
TABLE = {
    'spec_sha256': 'x' * 64,
    'enums': {'Mode': {'0': 'OFF', '2': 'ON'}},
    'messages': [
        {'name': 'MIXED', 'can_id': 0x100, 'quantity': 1, 'bus': 'Critical',
         'from_board': 'VCU', 'dlc': 8, 'frequency_hz': 100.0, 'signals': [
             {'name': 'speed', 'start_bit': 0, 'bit_length': 16, 'signed': True,
              'scale': 0.01, 'offset': 0.0, 'kind': 'physical', 'unit': 'rad/s'},
             {'name': 'count', 'start_bit': 16, 'bit_length': 8, 'signed': False,
              'scale': 1.0, 'offset': 0.0, 'kind': 'physical', 'unit': ''},
             {'name': 'on', 'start_bit': 24, 'bit_length': 1, 'signed': False,
              'scale': 1.0, 'offset': 0.0, 'kind': 'boolean', 'unit': ''},
             {'name': 'mode', 'start_bit': 32, 'bit_length': 8, 'signed': False,
              'scale': 1.0, 'offset': 0.0, 'kind': 'enum_type', 'unit': '', 'enum': 'Mode'},
             {'name': 'flags', 'start_bit': 40, 'bit_length': 8, 'signed': False,
              'scale': 1.0, 'offset': 0.0, 'kind': 'bitfield', 'unit': '',
              'bits': ['A', 'B', 'C']},
             {'name': 'temp', 'start_bit': 48, 'bit_length': 16, 'signed': False,
              'scale': 0.5, 'offset': -40.0, 'kind': 'physical', 'unit': 'degC'}]},
        {'name': 'CELLS', 'can_id': 0x200, 'quantity': 3, 'bus': 'Data Acq',
         'from_board': 'HVC', 'dlc': 2, 'frequency_hz': 10.0, 'signals': [
             {'name': 'v', 'start_bit': 0, 'bit_length': 16, 'signed': False,
              'scale': 0.0001, 'offset': 0.0, 'kind': 'physical', 'unit': 'V'}]},
    ],
}


@pytest.fixture
def table(tmp_path):
    path = tmp_path / 'table.json'
    path.write_text(json.dumps(TABLE))
    return CanTable(path)


def test_every_signal_kind_decodes(table):
    data = (-12345).to_bytes(2, 'little', signed=True) + bytes([200, 1, 2, 0b011]) + \
        (300).to_bytes(2, 'little')
    d = table.decode(0, 0x100, data)
    assert (d.message, d.bus) == ('MIXED', 'Critical')
    # Bit i is bits[i]: 0b011 reads differently in the other order.
    assert d.signals == {'speed': pytest.approx(-123.45), 'count': 200, 'on': True, 'mode': 2,
                         'mode_name': 'ON', 'flags': {'A': True, 'B': True, 'C': False},
                         'temp': 300 * 0.5 - 40.0}
    assert isinstance(d.signals['count'], int)


def test_an_offset_applies_at_unit_scale(tmp_path):
    t = json.loads(json.dumps(TABLE))
    t['messages'][0]['signals'][1]['offset'] = -40.0  # 'count'
    path = tmp_path / 'offset.json'
    path.write_text(json.dumps(t))
    data = bytes([0, 0, 100]) + bytes(5)
    assert CanTable(path).decode(0, 0x100, data).signals['count'] == 60.0


def test_unknown_values_ids_buses_and_short_frames(table):
    data = bytearray(8)
    data[4] = 1  # not a Mode value
    assert table.decode(0, 0x100, bytes(data)).signals['mode_name'] == '?'
    assert table.decode(0, 0x101, bytes(8)) is None
    assert table.decode(1, 0x100, bytes(8)) is None  # right ID, wrong bus
    assert table.decode(0, 0x100, bytes(7)) is None
    assert table.decode(7, 0x100, bytes(8)) is None


def test_message_blocks_carry_their_instance(table):
    d = table.decode(1, 0x202, (41000).to_bytes(2, 'little'))
    assert d.message == 'CELLS'
    assert d.signals == {'instance': 2, 'v': pytest.approx(4.1)}
    assert table.units('MIXED') == {'speed': 'rad/s', 'temp': 'degC'}


def test_the_generated_table_has_what_the_firmware_sends(can_table_path):
    table = CanTable(can_table_path)
    names = {(m['bus'], m['name']) for m in table.messages}
    for name in ('INVERTER_TORQUE_COMMAND', 'VCU_STATE', 'VCU_FAULTS', 'INVERTER_SPEED',
                 'CONTACTOR_STATUS'):
        assert ('Critical', name) in names, name
    assert ('Data Acq', 'ACCELERATION_VECTOR_UNSPRUNG_WHEEL_SPEED_RL') in names
    assert len(table.spec_sha256) == 64
    # Messages the SIL relies on must decode; others may be left raw.
    assert not set(table.unsupported) & {
        'INVERTER_TORQUE_COMMAND', 'VCU_STATE', 'VCU_FAULTS', 'INVERTER_SPEED'}
    assert {'VcuStateMachine', 'VcuFaultCode', 'ShutdownLoopState'} <= set(table.enums)


@dataclasses.dataclass(frozen=True)
class BrakeOverThrottle(Launch):
    """Half pedal with the brake hard on: the firmware's BOT fault."""
    duration_ms: int = 3600

    def at(self, t_ms):
        driver, car = super().at(t_ms)
        if t_ms >= 3000:
            driver = dataclasses.replace(driver, accelerator=0.5, brake_psi=900.0)
        return driver, car


def test_fault_frames_decode_to_the_faults_the_firmware_holds(lib, can_table_path):
    """VCU_FAULTS from the real firmware through the real table: the decoded
    bits name the same faults as the firmware's own snapshot."""
    table = CanTable(can_table_path)
    with Firmware(lib) as fw:
        result = cosim.run(BrakeOverThrottle(), fw, ToyCar())
        held = {}
        for snap in result.snapshots:
            held[int(snap['now_ms'])] = {n.lower() for n in fw.fault_names(int(snap['faults']))}
            primary = fw.fault_name(int(snap['primary_fault']))
    frames = [(t, table.decode(bus, fid, data)) for t, bus, fid, from_vcu, data in result.frames
              if from_vcu]
    faults = [(t, d.signals) for t, d in frames if d and d.message == 'VCU_FAULTS']
    assert len(faults) >= 30  # 10 Hz
    seen = set()
    for t, s in faults:
        on = {name for name, value in s['active_faults'].items() if value}
        # The frame carries the faults of the control tick that sent it.
        tick = max(k for k in held if k <= t)
        assert on == held[tick], t
        seen |= on
    assert {'shutdown_open', 'brake_over_throttle'} <= seen
    assert faults[-1][1]['primary_fault_name'] == primary == 'BRAKE_OVER_THROTTLE'
