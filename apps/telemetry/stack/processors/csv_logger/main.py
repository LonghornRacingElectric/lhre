"""Write one schema-ordered Orion CSV per connection, with recoverable offsets."""
import csv
import json
import logging
import os
from pathlib import Path
import re
import signal

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from kafka import KafkaConsumer
from stack.ingest.protobuf import can_packets_pb2


class SessionLogs:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.directory / '.csv_logger_checkpoint.json'
        self.state = {'files': {}, 'offsets': {}}
        if self.checkpoint_path.exists():
            self.state = json.loads(self.checkpoint_path.read_text())
        # Discard writes that never reached the durable checkpoint.
        for name, size in self.state['files'].items():
            path = self.directory / name
            if path.exists():
                if path.stat().st_size < size:
                    raise RuntimeError(f'Checkpointed CSV was truncated: {path}')
                with path.open('r+b') as output:
                    output.truncate(size)
            elif size:
                raise RuntimeError(f'Checkpointed CSV is missing: {path}')
        self.offsets = dict(self.state['offsets'])
        self.outputs = {}
        self.message = can_packets_pb2.OrionSensorData()
        self.columns = []
        for field in self.message.DESCRIPTOR.fields:
            if field.message_type:
                self.columns.extend((field.name, child.name) for child in field.message_type.fields)
            else:
                self.columns.append(('packet', field.name))

    def _save(self, state):
        temporary = self.checkpoint_path.with_suffix('.tmp')
        with temporary.open('w') as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.checkpoint_path)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.state = state

    def write(self, record):
        key = f'{record.topic}:{record.partition}'
        if record.offset < self.offsets.get(key, 0):
            return
        headers = dict(record.headers or [])
        session = headers.get('csv_session_id', b'').decode('ascii', errors='replace')
        if headers.get('car_type') != b'Orion' or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session):
            self.offsets[key] = record.offset + 1
            return
        try:
            self.message.ParseFromString(record.value)
        except DecodeError:
            logging.warning('Skipping undecodable frame at offset %d', record.offset)
            self.offsets[key] = record.offset + 1
            return
        name = f'orion_log_{session}.csv'
        if name not in self.outputs:
            if name not in self.state['files']:
                # Reserve the path before writing so recovery also knows about
                # a new session interrupted before its first checkpoint.
                self._save({**self.state, 'files': {**self.state['files'], name: 0}})
            path = self.directory / name
            output = path.open('a', newline='', encoding='utf-8')
            writer = csv.writer(output)
            if not path.stat().st_size:
                writer.writerow(f'{group}.{field}' for group, field in self.columns)
            self.outputs[name] = (output, writer)
        decoded = MessageToDict(self.message, preserving_proto_field_name=True,
                                always_print_fields_with_no_presence=True)
        row = []
        for group, field in self.columns:
            source = decoded if group == 'packet' else decoded.get(group, {})
            value = source.get(field, '')
            row.append(json.dumps(value) if isinstance(value, (list, dict)) else value)
        self.outputs[name][1].writerow(row)
        self.offsets[key] = record.offset + 1

    def checkpoint(self):
        sizes = dict(self.state['files'])
        for name, (output, _) in self.outputs.items():
            output.flush()
            os.fsync(output.fileno())
            sizes[name] = output.tell()
        self._save({'files': sizes, 'offsets': dict(self.offsets)})
        self.close()

    def close(self):
        for output, _ in self.outputs.values():
            output.close()
        self.outputs.clear()


def main():
    logging.basicConfig(level=os.getenv('LOGLEVEL', 'INFO'))
    stopped = False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logs = SessionLogs(Path(os.getenv('LOG_BASE_DIR', '/app/logs')) / 'orion')
    consumer = KafkaConsumer(
        os.getenv('KAFKA_INPUT_TOPIC', 'sensor_data'),
        bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092').split(','),
        group_id=os.getenv('KAFKA_GROUP_ID', 'telemetry-csv-logger-v1'),
        enable_auto_commit=False,
        auto_offset_reset='latest',
        max_poll_records=500,
    )
    try:
        while not stopped:
            batch = consumer.poll(timeout_ms=1000)
            if not batch:
                continue
            for records in batch.values():
                for record in records:
                    logs.write(record)
            logs.checkpoint()
            consumer.commit()
    finally:
        logs.close()
        consumer.close(autocommit=False)


if __name__ == '__main__':
    main()
