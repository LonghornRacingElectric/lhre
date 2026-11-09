"""Feed the UDP reader using current generated CAN IDs, layouts and rates."""
import argparse
import heapq
import json
import os
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--can-json', default=os.environ.get('CAN_JSON', str(Path(__file__).resolve().parents[4] / 'bazel-bin/lib/codegen/telemetry/can.json')))
    args = parser.parse_args()
    packets = json.loads(Path(args.can_json).read_text())
    queue = []
    for index, packet in enumerate(packets):
        if packet.get('frequency') and any(b.get('protobuf') or b.get('bitfield_encoding') for b in packet['bytes']):
            heapq.heappush(queue, (time.monotonic(), index, 0))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while queue:
            deadline, index, tick = heapq.heappop(queue)
            time.sleep(max(0, deadline - time.monotonic()))
            packet = packets[index]
            for offset in range(max(1, packet['quantity'])):
                payload = bytearray(packet['data_length'])
                for signal in packet['bytes']:
                    kind = signal['conv_type']
                    if kind == 'bitfield':
                        continue
                    signed = kind.startswith('int')
                    size = signal['length']
                    value = min(int((1 + tick % 20) / signal['precision']), (1 << (size * 8 - int(signed))) - 1)
                    start = signal['start_byte']
                    payload[start:start + size] = value.to_bytes(size, 'little', signed=signed)
                wire = (packet['packet_id'] + offset).to_bytes(4, 'little') + payload
                port = 5005 if packet['bus'] == 'Critical' else 5006
                sock.sendto(wire, ('127.0.0.1', port))
            period = 1 / packet['frequency']
            # Don't flood the reader with artificial catch-up after a pause.
            heapq.heappush(queue, (max(deadline + period, time.monotonic()), index, tick + 1))


if __name__ == '__main__':
    main()
