"""Decodes captured CAN frames with the table //boards/VCU:vcu_sil_can_table
generates from lib/spec, so names, scales and units are the spec's own.

The generator leaves out messages with big-endian or multiplexed signals
(listed under "unsupported"; their frames stay raw), so every signal here is
a little-endian bit range: shift, mask, sign-extend, scale. That matches the
generated C++ Unpack(); physical values are computed in float64, where the
C++ accessors round to float32.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

BUS_NAMES = {0: 'Critical', 1: 'Data Acq'}  # vcu_sil.h VCU_SIL_BUS_* -> spec bus


@dataclass(frozen=True)
class Decoded:
    message: str
    bus: str
    signals: dict[str, object]


class CanTable:
    def __init__(self, path: str | Path):
        with open(path, encoding='utf-8') as f:
            table = json.load(f)
        self.spec_sha256 = table['spec_sha256']
        self.enums = table['enums']
        self.messages = table['messages']
        # Messages the generator could not describe; their frames stay raw.
        self.unsupported: dict[str, list[str]] = table.get('unsupported', {})
        self._by_key: dict[tuple[str, int], dict] = {}
        for m in self.messages:
            for i in range(m['quantity']):
                self._by_key[(m['bus'], m['can_id'] + i)] = m

    def decode(self, bus: int, frame_id: int, data: bytes) -> Decoded | None:
        """None for an ID the spec does not have on that bus, or a short frame."""
        bus_name = BUS_NAMES.get(bus)
        m = self._by_key.get((bus_name, frame_id))
        if m is None or len(data) < m['dlc']:
            return None
        word = int.from_bytes(data[:m['dlc']], 'little')
        out: dict[str, object] = {}
        if m['quantity'] > 1:
            out['instance'] = frame_id - m['can_id']
        for s in m['signals']:
            start, n = s['start_bit'], s['bit_length']
            if s['kind'] == 'bitfield':
                out[s['name']] = {bit: bool(word >> (start + i) & 1)
                                  for i, bit in enumerate(s['bits'])}
                continue
            raw = word >> start & ((1 << n) - 1)
            if s['signed'] and raw >> (n - 1):
                raw -= 1 << n
            if s['kind'] == 'boolean':
                out[s['name']] = bool(raw)
            elif s['kind'] == 'enum_type':
                out[s['name']] = raw
                out[s['name'] + '_name'] = self.enums[s['enum']].get(str(raw), '?')
            elif s['scale'] == 1.0 and s['offset'] == 0.0:
                out[s['name']] = raw
            else:
                out[s['name']] = raw * s['scale'] + s['offset']
        return Decoded(m['name'], bus_name, out)

    def units(self, message: str) -> dict[str, str]:
        for m in self.messages:
            if m['name'] == message:
                return {s['name']: s['unit'] for s in m['signals'] if s['unit']}
        return {}
