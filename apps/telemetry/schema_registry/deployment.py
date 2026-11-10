"""Read the schema packaged with the running server, independent of checkout."""

import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


SCHEMA_FILES = ("schema_contract.json", "schema_descriptor.pb")
INSPECT_FORMAT = '{"id":{{json .Id}},"image_id":{{json .Image}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"mounts":{{json .Mounts}}}'


class DockerDeployment:
    def __init__(self, containers=("ingest", "kafka-bridge"), sudo=False, runner=subprocess.run):
        if len(containers) != 2 or len(set(containers)) != 2:
            raise ValueError("select distinct ingest and bridge containers")
        self.containers = tuple(containers)
        self.command = (["sudo"] if sudo else []) + ["docker"]
        self.runner = runner

    def run(self, *arguments):
        return self.runner(self.command + list(arguments), check=True, capture_output=True, text=True, timeout=30).stdout

    def inspect(self):
        states = [json.loads(line) for line in self.run("inspect", "--format", INSPECT_FORMAT, *self.containers).splitlines()]
        if len(states) != len(self.containers):
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

    def snapshot(self, configuration):
        from apps.telemetry.schema_registry.server import profile

        before = self.inspect()
        profiles = []
        with tempfile.TemporaryDirectory(prefix="telemetry-deployed-schema-") as temporary:
            for index, state in enumerate(before):
                directory = Path(temporary) / str(index)
                directory.mkdir()
                for name in SCHEMA_FILES:
                    self.run("cp", state["id"] + ":/schema/" + name, str(directory / name))
                profiles.append(profile(directory, configuration))
        after = self.inspect()
        if before != after:
            raise ValueError("server deployment changed while reading its schema; retry after deployment")
        if len({result["schema"]["schema_id"] for result, _ in profiles}) != 1:
            raise ValueError("running ingest and bridge schemas differ; finish deploying both")
        result, descriptor = profiles[0]
        result["deployment"] = {"consumers": [dict(name=name, **{key: state[key] for key in ("id", "image_id", "image")})
                                               for name, state in zip(self.containers, before)]}
        return result, descriptor
