"""Real Rust acquisition/publishing/logging against an isolated running stack."""
import csv
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from runfiles import Runfiles
from sqlalchemy import create_engine, select, URL
from sqlalchemy.orm import Session
from lib.telemetry.contract import Manifest, release_plan
from telemetry.stack.tests.test_utils import TelemetryConfig
from analysis.sql_utils.models import ArtemisControls333hz, ArtemisControls100hz, ArtemisPack1hz


class NativeBevoFlow(unittest.TestCase):
    def test_native_can_rates_reach_mqtt_kafka_timescale_and_pool_csvs(self):
        config = TelemetryConfig.from_env()
        files = Runfiles.Create()
        def artifact(name): return Path(files.Rlocation('_main/' + name))
        schema = Manifest(artifact('lib/codegen/telemetry/artemis_schema_contract.json').read_bytes())
        metadata = {'format_version':1,'schema_id':schema.schema_id,'projection_version':2,'wire_format':'artemis_updates_v1'}
        engine = create_engine(URL.create('postgresql+psycopg2',username=config.db_user,password=config.db_password,
            host=config.db_host,port=config.db_port,database='artemis'))
        processes = []
        start = int(time.time() * 1000)
        try:
            with tempfile.TemporaryDirectory(prefix='artemis-native-') as directory:
                root = Path(directory)
                (root/'pi_core_manifest.json').write_text(json.dumps(metadata))
                (root/'reconciliation.json').write_text(json.dumps(release_plan(metadata,schema,schema)))
                env = dict(os.environ,BEVO_CORE_RELEASE_ROOT=directory,LOGGERD_LOG_DIR=str(root/'logs'),
                    PUBLISHD_MQTT_HOST=config.mqtt_host,PUBLISHD_MQTT_PORT=str(config.mqtt_port),
                    CAND_NMEA_LISTEN_ADDR='127.0.0.1:0')
                for daemon in ('loggerd/loggerd','publishd/publishd','cand/mock_main'):
                    log = open(root/(daemon.split('/')[0]+'.log'),'w')
                    processes.append((subprocess.Popen([str(artifact('apps/BEVO/'+daemon))],env=env,stdout=log,stderr=log),log))
                # Bindings are local test-only sockets, never SocketCAN.
                ready = time.monotonic()+10
                while time.monotonic()<ready:
                    output=(root/'cand.log').read_text()
                    if 'Listening on 127.0.0.1:5005' in output and 'Listening on 127.0.0.1:5006' in output: break
                    time.sleep(.05)
                else: self.fail('UDP readers did not start: '+output)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                    def send(id,payload): udp.sendto(struct.pack('<I',id)+payload,('127.0.0.1',5005))
                    deadlines = sorted([(i/333,448,bytes(8)) for i in range(100)] +
                                       [(i/100,458,struct.pack('<HHHH',32,33,34,35)) for i in range(30)])
                    zero = time.monotonic()
                    for deadline,id,payload in deadlines:
                        time.sleep(max(0,zero+deadline-time.monotonic()))
                        send(id,payload)
                    for i in range(33): send(208+i,struct.pack('<HHHH',35000,35001,35002,35003))
                    # An incomplete next scan must not fabricate a complete array.
                    send(208,bytes(8))
                deadline = time.monotonic()+30
                while time.monotonic()<deadline:
                    with Session(engine) as db:
                        apps = db.execute(select(ArtemisControls333hz).where(ArtemisControls333hz.time>=start).order_by(ArtemisControls333hz.sample_time_ns)).scalars().all()
                        torque = db.execute(select(ArtemisControls100hz).where(ArtemisControls100hz.time>=start,ArtemisControls100hz.torque_lookup.is_not(None))).scalars().all()
                        cells = db.execute(select(ArtemisPack1hz).where(ArtemisPack1hz.time>=start,ArtemisPack1hz.cells_v.is_not(None))).scalars().all()
                    if len(apps)==100 and len(torque)==30 and len(cells)==1: break
                    if any(process.poll() is not None for process,_ in processes):
                        self.fail('\n'.join(p.read_text() for p in root.glob('*.log')))
                    time.sleep(.25)
                self.assertEqual(len(apps),100, f"torque={len(torque)} cells={len(cells)}; " + "\n".join(p.read_text() for p in root.glob("*.log")))
                self.assertEqual(len(torque),30)
                self.assertEqual(len(cells),1)
                self.assertTrue(all(row.apps1_travel==0 and row.enable is None for row in apps))
                self.assertTrue(all(row.torque_lookup>3.19 and row.bse3 is None for row in torque))
                # Graceful SIGTERM flushes buffered records before checking files.
                for process,_ in processes: process.terminate()
                for process,log in processes: process.wait(timeout=5); log.close()
                paths=list((root/'logs').glob('artemis_*_*.csv'))
                self.assertEqual(len({path.name.split('_')[1] for path in paths}),1)
                path=next(path for path in paths if path.name.endswith('_controls_333hz.csv'))
                with path.open() as file: rows=list(csv.DictReader(file))
                self.assertEqual(len(rows),100)
                self.assertEqual({int(row['sample_time_ns']) for row in rows},{row.sample_time_ns for row in apps})
                self.assertTrue(all(row['controls.enable']=='' for row in rows))
        finally:
            for process,log in processes:
                if process.poll() is None: process.terminate(); process.wait(timeout=5)
                log.close()
            engine.dispose()


if __name__=='__main__': unittest.main()
