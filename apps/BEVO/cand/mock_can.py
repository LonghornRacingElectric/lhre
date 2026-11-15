"""Feed cand over UDP or SocketCAN using generated CAN layouts and rates."""
import argparse
from contextlib import ExitStack
import heapq
import json
import math
import os
from pathlib import Path
import signal as signals
import socket
import struct
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--can-json', default=os.environ.get('CAN_JSON'))
    parser.add_argument('--transport', choices=('udp', 'socketcan'), default='udp')
    parser.add_argument('--critical-interface', default='vcan0')
    parser.add_argument('--data-interface', default='vcan1')
    parser.add_argument('--duration', type=float, help='Stop after this many seconds; otherwise run until interrupted.')
    args = parser.parse_args()
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error('--duration must be finite and positive')
    if args.transport == 'socketcan' and not hasattr(socket, 'AF_CAN'):
        parser.error('SocketCAN requires Linux; use UDP on other platforms')
    if args.can_json is None:
        args.can_json = Path(__file__).resolve().parents[3] / 'bazel-bin/lib/codegen/telemetry/can.json'
    packets = json.loads(Path(args.can_json).read_text())
    stopped = threading.Event()
    signals.signal(signals.SIGINT, lambda *_: stopped.set())
    signals.signal(signals.SIGTERM, lambda *_: stopped.set())
    started = time.monotonic()
    finish = started + args.duration if args.duration is not None else math.inf
    queue = []
    for index, packet in enumerate(packets):
        if packet.get('frequency') and any(b.get('protobuf') or b.get('bitfield_encoding') for b in packet['bytes']):
            heapq.heappush(queue, (started, index))
    if not queue:
        parser.error('CAN definitions contain no periodic telemetry frames')
    with ExitStack() as stack:
        if args.transport == 'udp':
            udp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        else:
            buses = {}
            for bus, interface in [('Critical', args.critical_interface), ('Data Acq', args.data_interface)]:
                buses[bus] = stack.enter_context(socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW))
                buses[bus].bind((interface,))
        frames = 0
        print(json.dumps({'transport': args.transport, 'can_json': str(Path(args.can_json).resolve()),
                          'start_ms': time.time_ns() // 1_000_000, 'duration_s': args.duration}), flush=True)
        while queue and not stopped.is_set():
            deadline, index = heapq.heappop(queue)
            if stopped.wait(max(0, min(deadline, finish) - time.monotonic())) or time.monotonic() >= finish:
                break
            packet = packets[index]
            # A shared ten-second ramp stays visible across different native rates.
            phase = (time.monotonic() - started) % 10 / 5
            fraction = phase if phase <= 1 else 2 - phase
            for offset in range(max(1, packet['quantity'])):
                payload = bytearray(packet['data_length'])
                for signal in packet['bytes']:
                    kind = signal['conv_type']
                    signed = kind.startswith('int')
                    size = signal['length']
                    if kind == 'bitfield':
                        value = sum(1 << bit['bit_index'] for bit in signal.get('bitfield_encoding', []) or []) if fraction >= .5 else 0
                    elif signal.get('is_boolean'):
                        value = int(fraction >= .5)
                    else:
                        maximum = min(int(20 / signal['precision']), (1 << (size * 8 - int(signed))) - 1)
                        value = round(fraction * maximum)
                    start = signal['start_byte']
                    payload[start:start + size] = value.to_bytes(size, 'little', signed=signed)
                can_id = packet['packet_id'] + offset
                if args.transport == 'udp':
                    wire = can_id.to_bytes(4, 'little') + payload
                    port = 5005 if packet['bus'] == 'Critical' else 5006
                    udp.sendto(wire, ('127.0.0.1', port))
                else:
                    if len(payload) > 8:
                        raise ValueError('SocketCAN demo supports classic CAN payloads up to 8 bytes')
                    if can_id > 0x7ff:
                        can_id |= socket.CAN_EFF_FLAG
                    buses[packet['bus']].send(struct.pack('=IB3x8s', can_id, len(payload), payload))
                frames += 1
            period = 1 / packet['frequency']
            # Don't flood the reader with artificial catch-up after a pause.
            heapq.heappush(queue, (max(deadline + period, time.monotonic()), index))
        print(json.dumps({'frames_sent': frames, 'stopped': True}), flush=True)


if __name__ == '__main__':
    main()
