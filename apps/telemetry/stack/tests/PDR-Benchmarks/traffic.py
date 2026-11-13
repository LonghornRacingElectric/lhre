"""Native Artemis and legacy Orion encodings of the same native observations."""
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

from google.protobuf import descriptor_pool, message_factory
from stack.ingest.protobuf import artemis_pb2
import common as c


def legacy_message(root):
    source = root / "telemtry/stack/ingest/protobuf/can_packets_pb2.py"
    tree = ast.parse(source.read_text())
    serialized = next(ast.literal_eval(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "AddSerializedFile")
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(serialized)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("orion.OrionSensorData"))


def comparable_pools(catalog, legacy):
    result, excluded = [], []
    groups = legacy.DESCRIPTOR.fields_by_name
    for pool in catalog["pools"]:
        group = groups.get(pool["group"])
        fields = [] if group is None else [f for f in pool["fields"] if f["name"] in group.message_type.fields_by_name]
        # Prefer a nonzero numeric identity marker; arrays/bools also have markers
        # distinct from the defaults materialized by the old packet decoder.
        fields.sort(key=lambda f: (f["sql_type"] == "boolean", f["sql_type"].endswith("[]")))
        if fields and pool["frequency_hz"]:
            result.append({**pool, "fields": fields})
        else:
            excluded.append(pool["table"])
    return result, excluded


def expected_values(pool, index):
    scalar = float(index % 99 + 1)
    return {f["name"]: ([scalar, 0., 3.5] if f["sql_type"].endswith("[]") else True if f["sql_type"] == "boolean" else scalar) for f in pool["fields"]}


def encode(pool, ns, index, packet_id, legacy=None):
    expected = expected_values(pool, index)
    if legacy:
        message = legacy(time=ns // 1_000_000, packet_id=packet_id)
        values = getattr(message, pool["group"])
    else:
        message = artemis_pb2.TelemetryBatch(format_version=1)
        update = message.updates.add(sample_time_ns=ns)
        update.values.time = ns // 1_000_000
        group = update.values.DESCRIPTOR.fields_by_name[pool["group"]]
        values = getattr(update.values, pool["group"])
    for name, value in expected.items():
        if isinstance(value, list):
            getattr(values, name).extend(value)
        else:
            setattr(values, name, value)
        if not legacy:
            update.updated_fields.add(group=group.number, field=group.message_type.fields_by_name[name].number)
    return message.SerializeToString(), expected


def build_legacy(root, fix_diagnostics=False):
    """Build actual old sources without sending its .env or unrelated files to Docker."""
    manifest = {}
    patch = None
    ingest_image = "lhre/pdr-legacy-ingest:diagnostics-fixed" if fix_diagnostics else "lhre/pdr-legacy-ingest:local"
    with tempfile.TemporaryDirectory(prefix="pdr-legacy-") as temp:
        destination = Path(temp)
        for directory in ("analysis/sql_utils", "stack/ingest"):
            for source in (root / "telemtry" / directory).rglob("*.py"):
                if "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(root / "telemtry")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                manifest[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        for name in ("net_configs.json", "stack/ingest/requirements.txt"):
            source = root / "telemtry" / name
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        if fix_diagnostics:
            target = destination / "analysis/sql_utils/query_builder.py"
            original = target.read_text()
            before = "OrionPacket, OrionDynamics, OrionControls, OrionPack, OrionDiagnosticsLow, OrionThermal,"
            after = "OrionPacket, OrionDynamics, OrionControls, OrionPack, OrionDiagnosticsHigh, OrionDiagnosticsLow, OrionThermal,"
            if original.count(before) != 1:
                raise RuntimeError("Legacy diagnostics fix no longer matches this checkout")
            target.write_text(original.replace(before, after))
            patch = {"file": "analysis/sql_utils/query_builder.py", "before": before, "after": after, "patched_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        # Preserve the original image definition and its dependency versions.
        shutil.copyfile(root / "telemtry/stack/ingest/Dockerfile", destination / "Dockerfile")
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", ingest_image, str(destination)], check=True)
    with tempfile.TemporaryDirectory(prefix="pdr-legacy-bridge-") as temp:
        destination = Path(temp)
        source_root = root / "telemtry/stack/kafka"
        sources = [source_root / name for name in ("Dockerfile", "go.mod", "go.sum")]
        sources += list((source_root / "cmd").rglob("*.go"))
        sources += list((source_root / "proto").rglob("*.proto"))
        for source in sources:
            relative = source.relative_to(source_root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest["kafka/" + str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", "lhre/pdr-legacy-bridge:local", str(destination)], check=True)
    return {"commit": c.command("git", "-C", str(root), "rev-parse", "HEAD"), "source_sha256": manifest, "benchmark_only_patch": patch, "ingest_image": ingest_image}


def initialize_legacy(root):
    container = c.compose("ps", "-q", "db")
    c.command("docker", "exec", container, "psql", "-U", "electric", "-d", "postgres", "-c", "CREATE DATABASE orion")
    with (root / "telemtry/stack/ingest/orion_db_init.sql").open() as source:
        subprocess.run(["docker", "exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "electric", "-d", "orion"], stdin=source, stdout=subprocess.DEVNULL, check=True)
