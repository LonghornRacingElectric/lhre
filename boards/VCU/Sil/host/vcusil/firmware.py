"""ctypes binding for libvcu_sil.so, the VCU firmware behind a C ABI.

The structs mirror boards/VCU/Sil/vcu_sil.h. Loading the library checks the
mirror field by field against the library's own layout table, so a header
change that is not repeated here fails at load time, not as wrong numbers.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char, c_char_p, c_double, c_int32, c_uint32
from pathlib import Path

ABI_VERSION = 1

# lhre VcuStateMachine / HvcState values (lib/spec/types.textproto).
STATE_DRIVE = 2
HVC_NOT_ENERGIZED = 0
HVC_PRECHARGING = 1
HVC_ENERGIZED = 2

# vcu_sil.h VCU_SIL_SAT_* bits.
SATURATION_BITS = {
    'wheel_speed': 1 << 0,
    'motor_speed': 1 << 1,
    'motor_torque': 1 << 2,
    'bus_voltage': 1 << 3,
    'bus_current': 1 << 4,
    'cell_voltage': 1 << 5,
    'steering': 1 << 6,
}


class Config(ctypes.Structure):
    _fields_ = [
        ('struct_size', c_uint32),
        ('event_mode', c_int32),
    ]


class Inputs(ctypes.Structure):
    _fields_ = [
        ('struct_size', c_uint32),
        ('rtd_pressed', c_int32),
        ('accelerator_travel', c_double),
        ('brake_front_psi', c_double),
        ('brake_rear_psi', c_double),
        ('steering_angle_deg', c_double),
        ('hvc_state', c_int32),
        ('contactors_closed', c_int32),
        ('shutdown_loop_v', c_double),
        ('wheel_speed_rad_s', c_double * 4),
        ('motor_speed_rad_s', c_double),
        ('motor_torque_nm', c_double),
        ('dc_bus_voltage_v', c_double),
        ('dc_bus_current_a', c_double),
        ('min_cell_v', c_double),
        ('max_cell_v', c_double),
    ]

    def __init__(self, **values):
        super().__init__(struct_size=ctypes.sizeof(Inputs), **values)


class Outputs(ctypes.Structure):
    _fields_ = [
        ('struct_size', c_uint32),
        ('now_ms', c_uint32),
        ('control_ms', c_uint32),
        ('torque_frames', c_uint32),
        ('inverter_enable', c_int32),
        ('direction', c_int32),
        ('torque_request_nm', c_double),
        ('torque_limit_nm', c_double),
        ('saturated', c_uint32),
        ('state', c_int32),
        ('faults', c_uint32),
        ('primary_fault', c_int32),
        ('event_mode', c_int32),
        ('loop_state', c_int32),
        ('motor_speed_valid', c_int32),
        ('adc_fresh', c_int32),
        ('wheel_control_valid', c_int32),
        ('buzzer', c_int32),
        ('hard_braking', c_int32),
        ('pedal_travel', c_double),
        ('apps1_travel', c_double),
        ('apps2_travel', c_double),
        ('bse1_psi', c_double),
        ('bse2_psi', c_double),
        ('bse_filtered_psi', c_double),
        ('bse3_psi', c_double),
        ('steering_angle_deg', c_double),
        ('motor_rpm', c_double),
        ('torque_lookup_nm', c_double),
        ('torque_derated_nm', c_double),
        ('torque_power_limited_nm', c_double),
        ('torque_traction_nm', c_double),
        ('torque_command_nm', c_double),
        ('power_w', c_double),
        ('power_trim_w', c_double),
        ('soe_pct', c_double),
        ('wheel_reference_m_s', c_double),
        ('wheel_drive_slip', c_double),
    ]

    def __init__(self):
        super().__init__(struct_size=ctypes.sizeof(Outputs))


class _Field(ctypes.Structure):
    _fields_ = [
        ('structure', c_char_p),
        ('field', c_char_p),
        ('offset', c_uint32),
        ('size', c_uint32),
    ]


MIRRORS = {'config': Config, 'inputs': Inputs, 'outputs': Outputs}

_OK = 0
_ERR_RANGE = -4


class FirmwareError(RuntimeError):
    """The library rejected a call or does not match this binding."""


def _declare(lib: ctypes.CDLL) -> None:
    handle = ctypes.c_void_p
    signatures = {
        'vcu_sil_abi_version': ([], c_int32),
        'vcu_sil_create': ([POINTER(Config), POINTER(handle)], c_int32),
        'vcu_sil_destroy': ([handle], None),
        'vcu_sil_advance': ([handle, POINTER(Inputs), c_uint32, POINTER(Outputs)], c_int32),
        'vcu_sil_read_console': (
            [handle, POINTER(c_char), c_uint32, POINTER(c_uint32)], c_int32),
        'vcu_sil_layout': ([c_uint32, POINTER(_Field)], c_int32),
        'vcu_sil_build_git_sha': ([], c_char_p),
        'vcu_sil_build_git_describe': ([], c_char_p),
        'vcu_sil_build_dirty': ([], c_int32),
        'vcu_sil_state_name': ([c_int32], c_char_p),
        'vcu_sil_fault_name': ([c_int32], c_char_p),
        'vcu_sil_error_name': ([c_int32], c_char_p),
    }
    for name, (argtypes, restype) in signatures.items():
        try:
            function = getattr(lib, name)
        except AttributeError as exc:
            raise FirmwareError(f'{lib._name} is not libvcu_sil.so: no {name}') from exc
        function.argtypes = argtypes
        function.restype = restype


def library_layout(lib: ctypes.CDLL) -> dict[str, dict[str, tuple[int, int]]]:
    """{struct: {field: (offset, size)}}, with '' for the whole struct."""
    layout: dict[str, dict[str, tuple[int, int]]] = {}
    entry = _Field()
    index = 0
    while (status := lib.vcu_sil_layout(index, byref(entry))) == _OK:
        layout.setdefault(entry.structure.decode(), {})[entry.field.decode()] = (
            entry.offset, entry.size)
        index += 1
    if status != _ERR_RANGE:
        raise FirmwareError(f'vcu_sil_layout({index}) returned {status}')
    return layout


def check_mirror(layout: dict[str, dict[str, tuple[int, int]]],
                 mirrors: dict[str, type[ctypes.Structure]] = MIRRORS) -> None:
    """Raise FirmwareError unless every mirror matches the library exactly."""
    problems = []
    if set(layout) != set(mirrors):
        problems.append(f'structs {sorted(layout)} != {sorted(mirrors)}')
    for name, mirror in mirrors.items():
        fields = layout.get(name, {})
        ours = {f: (getattr(mirror, f).offset, getattr(mirror, f).size)
                for f, _ in mirror._fields_}
        ours[''] = (0, ctypes.sizeof(mirror))
        for field in sorted(set(fields) | set(ours)):
            if fields.get(field) != ours.get(field):
                problems.append(
                    f'{name}.{field or "<struct>"}: library {fields.get(field)} '
                    f'!= binding {ours.get(field)}')
    if problems:
        raise FirmwareError('vcu_sil.h and firmware.py disagree:\n  ' + '\n  '.join(problems))


def load_library(path: str | Path) -> ctypes.CDLL:
    lib = ctypes.CDLL(str(path))
    _declare(lib)
    version = lib.vcu_sil_abi_version()
    if version != ABI_VERSION:
        raise FirmwareError(f'{path} has ABI version {version}, this binding is {ABI_VERSION}')
    check_mirror(library_layout(lib))
    return lib


class Firmware:
    """One VCU instance: the unmodified app on host fakes, stepped in ms."""

    def __init__(self, lib: ctypes.CDLL, event_mode: int = 0):
        self._lib = lib
        self._handle = ctypes.c_void_p()
        config = Config(struct_size=ctypes.sizeof(Config), event_mode=event_mode)
        self._check(lib.vcu_sil_create(byref(config), byref(self._handle)), 'create')

    def advance(self, inputs: Inputs, ms: int) -> Outputs:
        out = Outputs()
        self._check(self._lib.vcu_sil_advance(self._live(), byref(inputs), ms, byref(out)),
                    'advance')
        return out

    def read_console(self) -> str:
        chunk = 64 * 1024
        buffer = ctypes.create_string_buffer(chunk)
        length = c_uint32()
        parts = []
        while True:
            self._check(self._lib.vcu_sil_read_console(
                self._live(), buffer, chunk, byref(length)), 'read_console')
            if length.value == 0:
                return b''.join(parts).decode(errors='replace')
            parts.append(buffer.raw[:length.value])

    def state_name(self, state: int) -> str:
        return self._lib.vcu_sil_state_name(state).decode()

    def fault_name(self, code: int) -> str:
        return self._lib.vcu_sil_fault_name(code).decode()

    def fault_names(self, faults: int) -> list[str]:
        """Names of the set bits; bit i is fault code i + 1."""
        return [self.fault_name(i + 1) for i in range(32) if faults >> i & 1]

    def build_info(self) -> dict[str, object]:
        return {
            'git_sha': self._lib.vcu_sil_build_git_sha().decode(),
            'git_describe': self._lib.vcu_sil_build_git_describe().decode(),
            'dirty': bool(self._lib.vcu_sil_build_dirty()),
        }

    def close(self) -> None:
        if self._handle:
            self._lib.vcu_sil_destroy(self._handle)
            self._handle = ctypes.c_void_p()

    def __enter__(self) -> Firmware:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _live(self) -> ctypes.c_void_p:
        if not self._handle:
            raise FirmwareError('firmware instance is closed')
        return self._handle

    def _check(self, status: int, call: str) -> None:
        if status != _OK:
            name = self._lib.vcu_sil_error_name(status).decode()
            raise FirmwareError(f'vcu_sil_{call} failed: {name} ({status})')
