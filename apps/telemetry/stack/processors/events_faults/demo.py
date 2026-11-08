"""Exercise real protobuf decoding and transitions without a Kafka broker."""

import json
from types import SimpleNamespace

from stack.ingest.protobuf import can_packets_pb2

from processor import FaultProcessor


class PrintProducer:
    def send(self, topic, value, key):
        print(json.dumps(value, sort_keys=True))
        return SimpleNamespace(get=lambda timeout: None)


def main():
    processor = FaultProcessor(PrintProducer())
    # The repeated active reading must not emit another raised event.
    for offset, (stomp, inverter) in enumerate(
        [(False, 0), (True, 0), (True, 0), (False, 0), (False, 4), (False, 0)]
    ):
        frame = can_packets_pb2.OrionSensorData(
            packet_id=offset + 1, time=1767225600000 + offset * 1000,
        )
        frame.diagnostics_high.stomp_fault = stomp
        frame.diagnostics_high.run_faults = inverter
        processor.process(SimpleNamespace(
            topic="demo_sensor_data", partition=0, offset=offset,
            headers=[("car_type", b"Orion")], value=frame.SerializeToString(),
        ))


if __name__ == "__main__":
    main()
