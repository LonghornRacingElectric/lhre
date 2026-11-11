"""Read the schema packaged with the running server, independent of checkout."""

import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


SCHEMA_FILES = ("schema_contract.json", "schema_descriptor.pb")
INSPECT_FORMAT = '{"id":{{json .Id}},"image_id":{{json .Image}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"mounts":{{json .Mounts}}}'


class DockerDeployment:
    def __init__(self, containers=("ingest", "kafka-bridge"), sudo=False, runner=subprocess.run, network=None):
        if len(containers) < 2 or len(set(containers)) != len(containers):
            raise ValueError("select distinct schema consumers")
        self.containers = tuple(containers)
        self.network = network
        self.command = (["sudo"] if sudo else []) + ["docker"]
        self.runner = runner

    def run(self, *arguments):
        return self.runner(self.command + list(arguments), check=True, capture_output=True, text=True, timeout=30).stdout

    def inspect(self):
        names = self.names()
        states = [json.loads(line) for line in self.run("inspect", "--format", INSPECT_FORMAT, *names).splitlines()]
        if len(states) != len(names):
            raise ValueError("missing deployed schema consumer")
        for state in states:
            if not state["running"] or state["restarting"]:
                raise ValueError("schema consumer is not running steadily")
            for mount in state.get("mounts") or []:
                destination = PurePosixPath(mount["Destination"])
                schema = PurePosixPath("/schema")
                if destination == schema or destination in schema.parents or schema in destination.parents:
                    raise ValueError("deployed schema metadata is shadowed by a mount")
        return states

    def names(self):
        if not self.network:
            return self.containers
        active = self.run("ps", "--filter", "label=lhre.schema-consumer=artemis", "--filter", "network=" + self.network, "--format", "{{.Names}}").splitlines()
        known = {"board_health_processor", "events_faults_processor", "car_status_processor", "gps_classifier_processor",
                 "lap_timer_processor", "kafka_processor_track_mapper", "kafka_processor_gg_plot"}
        running = self.run("ps", "--filter", "network=" + self.network, "--format", "{{.Names}}").splitlines()
        return tuple(sorted(set(self.containers) | set(active) | (known & set(running))))

    def snapshot(self, configuration):
        from apps.telemetry.schema_registry.server import profile

        before = self.inspect()
        profiles = []
        with tempfile.TemporaryDirectory(prefix="telemetry-deployed-schema-") as temporary:
            for index, state in enumerate(before):
                directory = Path(temporary) / str(index)
                directory.mkdir()
                names = SCHEMA_FILES + (("can.json", "storage_contract.json") if self.network else ())
                for name in names:
                    self.run("cp", state["id"] + ":/schema/" + name, str(directory / name))
                profiles.append(profile(directory, configuration))
        after = self.inspect()
        if before != after:
            raise ValueError("server deployment changed while reading its schema; retry after deployment")
        if len({(result["schema"]["schema_id"], result.get("can_json_sha256"), result.get("storage_id")) for result, _ in profiles}) != 1:
            raise ValueError("running ingest and bridge schemas differ; finish deploying both")
        result, descriptor = profiles[0]
        result["deployment"] = {"consumers": [dict(name=name, **{key: state[key] for key in ("id", "image_id", "image")})
                                               for name, state in zip(self.names(), before)]}
        return result, descriptor
