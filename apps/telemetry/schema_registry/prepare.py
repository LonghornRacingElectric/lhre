"""Discover releases or prepare additive storage before operator deployment."""

import argparse
import hashlib
import json
from pathlib import Path

from apps.telemetry.schema_registry.catalog import Catalog, atomic_json
from apps.telemetry.schema_registry.deployment import DockerDeployment
from lib.telemetry.contract import Manifest, Schema, compare
from telemetry.scripts.apply_storage import apply, changes, database_session, ArtemisStorageMigration
from sqlalchemy import select


def prepare(catalog, reference, built_directory, deployment, rollback=False):
    with catalog.locked():
        return _prepare(catalog, reference, built_directory, deployment, rollback)


def _prepare(catalog, reference, built_directory, deployment, rollback):
    files, release, schema, storage = catalog.read(reference)
    built_directory = Path(built_directory)
    built = Schema((built_directory / "artemis_schema_contract.json").read_bytes(),
                   (built_directory / "artemis_schema_descriptor.pb").read_bytes())
    if built.schema_id != schema.schema_id:
        raise ValueError("built server protobuf differs from selected release")
    if hashlib.sha256((built_directory / "can.json").read_bytes()).hexdigest() != release["can_json_sha256"] or json.loads((built_directory / "storage_contract.json").read_bytes())["storage_id"] != release["storage_id"]:
        raise ValueError("built server CAN/storage definitions differ from selected release")
    if deployment is not None:
        previous, _ = deployment.snapshot({"releases": []})
        source = Manifest(previous["schema"])
        if rollback:
            if reference not in (catalog.index().get("active"), catalog.index().get("previous")):
                raise ValueError("rollback must select a previously active cached release")
            source, schema = schema, source
        _, rejected = compare(source, schema)
        if rejected or any(set(field.get("enum", {}).items()) - set(schema.fields[path].get("enum", {}).items())
                           for path, field in source.fields.items()):
            raise ValueError("deployed field meaning/type/name/layout changed; explicit migration required")
    with database_session() as session, session.begin():
        if deployment is None:
            from sqlalchemy import inspect
            if inspect(session.connection()).has_table("storage_migration", schema="public"):
                raise ValueError("bootstrap requires an uninitialized database; use deployed schema comparison")
        if rollback:
            if not session.scalar(select(ArtemisStorageMigration.storage_id).where(ArtemisStorageMigration.storage_id == storage["storage_id"])):
                raise ValueError("rollback storage was never applied")
            planned = changes(session.connection(), storage, allow_historical=True)
            if planned:
                raise ValueError("rollback requires intact historical storage")
        else:
            planned = apply(session, storage, files["storage.sql"].decode())
    index = catalog.index()
    index["prepared"] = reference
    atomic_json(catalog.root / "index.json", index)
    return {"prepared": reference, "storage_id": storage["storage_id"], "changes": planned}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "prepare"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference")
    parser.add_argument("--built-schema-directory", type=Path)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--docker-sudo", action="store_true")
    args = parser.parse_args()
    configuration = json.loads(args.config.read_bytes())
    catalog = Catalog(configuration["catalog"])
    if args.command == "sync":
        print(catalog.sync(args.reference))
    else:
        if not args.reference or not args.built_schema_directory:
            parser.error("prepare requires a digest --reference and --built-schema-directory")
        catalog.directory(args.reference)
        catalog.sync(args.reference)
        deployment = None if args.bootstrap else DockerDeployment(sudo=args.docker_sudo)
        if args.bootstrap and args.rollback:
            parser.error("bootstrap and rollback cannot be combined")
        print(json.dumps(prepare(catalog, args.reference, args.built_schema_directory, deployment, args.rollback)))


if __name__ == "__main__":
    main()
