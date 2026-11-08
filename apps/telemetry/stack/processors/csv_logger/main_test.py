import csv
import tempfile
import queue
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from stack.ingest.protobuf import can_packets_pb2
from stack.processors.csv_logger import main as logger
from stack.ingest import mqtt_handler


def record(offset, session=b'first', packet_id=None, car=b'Orion', value=None):
    packet = can_packets_pb2.OrionSensorData(packet_id=offset + 1 if packet_id is None else packet_id)
    return SimpleNamespace(topic='sensor_data', partition=0, offset=offset,
                           headers=[('car_type', car), ('csv_session_id', session)],
                           value=packet.SerializeToString() if value is None else value)


class SessionLogsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.logs = logger.SessionLogs(self.directory)
        self.addCleanup(self.logs.close)

    def rows(self, session):
        with (self.directory / f'orion_log_{session}.csv').open(newline='') as source:
            return list(csv.DictReader(source))

    def restart(self):
        self.logs.close()
        self.logs = logger.SessionLogs(self.directory)
        self.addCleanup(self.logs.close)

    def test_connection_sessions_split_even_in_one_polled_batch(self):
        self.logs.write(record(0))
        self.logs.write(record(1))
        self.logs.write(record(2, session=b'reconnected'))
        self.logs.checkpoint()
        self.assertEqual([r['packet.packet_id'] for r in self.rows('first')], ['1', '2'])
        self.assertEqual([r['packet.packet_id'] for r in self.rows('reconnected')], ['3'])

    def test_restart_resumes_session_and_skips_checkpointed_replay(self):
        self.logs.write(record(0))
        self.logs.checkpoint()
        # Crash after durable CSV/checkpoint but before Kafka commit.
        self.restart()
        self.logs.write(record(0))
        self.logs.write(record(1))
        self.logs.checkpoint()
        self.assertEqual([r['packet.packet_id'] for r in self.rows('first')], ['1', '2'])
        self.assertEqual(len(list(self.directory.glob('*.csv'))), 1)

    def test_uncheckpointed_rows_are_rolled_back_before_replay(self):
        self.logs.write(record(0))
        self.logs.checkpoint()
        self.logs.write(record(1))
        self.logs.close()  # flush without checkpoint, as during a crash
        self.restart()
        self.logs.write(record(1))
        self.logs.checkpoint()
        self.assertEqual([r['packet.packet_id'] for r in self.rows('first')], ['1', '2'])

    def test_crash_during_first_write_of_new_session(self):
        self.logs.write(record(0))
        self.logs.write(record(1, session=b'second'))
        self.restart()
        self.logs.write(record(0))
        self.logs.write(record(1, session=b'second'))
        self.logs.checkpoint()
        self.assertEqual(len(self.rows('first')), 1)
        self.assertEqual(len(self.rows('second')), 1)

    def test_non_orion_sessionless_and_malformed_frames_do_not_create_files(self):
        self.logs.write(record(0, car=b'Angelique'))
        self.logs.write(record(1, session=b''))
        self.logs.write(record(2, value=b'\xff'))
        self.logs.checkpoint()
        self.assertEqual(list(self.directory.glob('*.csv')), [])
        self.assertEqual(self.logs.state['offsets'], {'sensor_data:0': 3})

    def test_new_consumer_group_starts_at_live_end(self):
        with patch.object(logger, 'KafkaConsumer') as consumer, \
             patch.object(logger, 'SessionLogs'), patch.object(logger.signal, 'signal'):
            consumer.return_value.poll.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                logger.main()
            self.assertEqual(consumer.call_args.kwargs['auto_offset_reset'], 'latest')
            consumer.return_value.commit.assert_not_called()
            consumer.return_value.close.assert_called_once_with(autocommit=False)

    def test_checkpoint_failure_does_not_advance_durable_offsets(self):
        self.logs.write(record(0))
        with patch.object(logger.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.logs.checkpoint()
        self.restart()
        self.logs.write(record(0))
        self.logs.checkpoint()
        self.assertEqual(len(self.rows('first')), 1)


class ConnectionSessionTest(unittest.TestCase):
    def test_announcements_capture_session_before_forwarding_queue_drains(self):
        handler = object.__new__(mqtt_handler.MQTTHandler)
        handler.orion_csv_session = ''
        handler.bridge_queue = queue.Queue()
        handler.bridge_stub = Mock()
        handler.bridge_stub.SendSensorData.return_value.success = True
        userdata = SimpleNamespace(client=Mock())
        with patch.object(mqtt_handler, 'QueryBuilder') as query:
            query.return_value.__enter__.return_value.session.query.return_value.order_by.return_value.first.return_value = None
            handler.on_message(None, userdata, SimpleNamespace(topic='client-connections', payload=b'BEVO-Orion'))
            first = handler.orion_csv_session
            handler._send_to_bridge(b'first packet', 'Orion')
            handler.on_message(None, userdata, SimpleNamespace(topic='client-connections', payload=b'BEVO_ORION'))
            second = handler.orion_csv_session
            handler._send_to_bridge(b'second packet', 'Orion')
        self.assertNotEqual(first, second)
        handler.bridge_queue.put(None)
        handler._bridge_worker()
        calls = handler.bridge_stub.SendSensorData.call_args_list
        self.assertEqual(calls[0].kwargs['metadata'], (('csv-session-id', first),))
        self.assertEqual(calls[1].kwargs['metadata'], (('csv-session-id', second),))


if __name__ == '__main__':
    unittest.main()
