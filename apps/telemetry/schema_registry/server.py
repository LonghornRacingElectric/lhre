"""Read-only server schema authority for the separate BEVO updater."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import logging

from runfiles import Runfiles

from lib.telemetry.contract import Schema, canonical_json
from apps.telemetry.schema_registry.deployment import DockerDeployment
from apps.telemetry.schema_registry.catalog import Catalog


def profile(schema_directory, configuration):
    schema_directory = Path(schema_directory)
    contract = "schema_contract.json" if (schema_directory / "schema_contract.json").exists() else "artemis_schema_contract.json"
    descriptor = "schema_descriptor.pb" if contract == "schema_contract.json" else "artemis_schema_descriptor.pb"
    schema = Schema((schema_directory / contract).read_bytes(), (schema_directory / descriptor).read_bytes())
    releases = configuration.get("releases", [])
    if not isinstance(releases, list) or len(releases) > 32 or any(not isinstance(reference, str) or not re.fullmatch(r"ghcr\.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}", reference) for reference in releases):
        raise ValueError("releases must be an ordered list of at most 32 digest-pinned GHCR images")
    result = {
        "format_version": 1, "schema": schema.manifest,
        "schema_descriptor_sha256": hashlib.sha256(schema.descriptor_set).hexdigest(),
        "releases": releases,
    }
    if (schema_directory / "can.json").exists():
        result["can_json_sha256"] = hashlib.sha256((schema_directory / "can.json").read_bytes()).hexdigest()
        storage = json.loads((schema_directory / "storage_contract.json").read_bytes())
        result["storage_id"] = storage["storage_id"]
    return result, schema.descriptor_set


def storage_ready(storage_id, url=None):
    from sqlalchemy import create_engine, MetaData, Table, select
    url = url or os.environ["ARTEMIS_DATABASE_URL"]
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            table = Table("storage_migration", MetaData(), schema="public", autoload_with=connection)
            if connection.scalar(select(table.c.storage_id).where(table.c.storage_id == storage_id)) != storage_id:
                raise ValueError("deployed storage schema is not applied")
    finally:
        engine.dispose()


def create_server(provider, address):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/profile.json", "/schema_descriptor.pb"):
                self.send_error(404)
                return
            try:
                configuration, descriptor = provider()
            except Exception as error:
                self.log_error("Cannot read authoritative deployed schema: %s", error)
                self.send_error(503, "Deployed schema unavailable; check both schema consumers")
                return
            if self.path == "/profile.json":
                content, mime = canonical_json(configuration).encode(), "application/json"
            else:
                content, mime = descriptor, "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=os.getenv("TELEMETRY_SCHEMA_RELEASES"), required=not os.getenv("TELEMETRY_SCHEMA_RELEASES"))
    parser.add_argument("--schema-directory", type=Path, default=os.getenv("TELEMETRY_SERVER_SCHEMA_DIR"))
    parser.add_argument("--deployed", action="store_true", help="read schema from running ingest and bridge containers on this host")
    parser.add_argument("--docker-sudo", action="store_true")
    parser.add_argument("--ingest-container", default="ingest")
    parser.add_argument("--bridge-container", default="kafka-bridge")
    parser.add_argument("--network", default="telemetry_network")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8085)
    args = parser.parse_args()
    configuration = json.loads(args.config.read_text())
    catalog = Catalog(configuration["catalog"]) if "catalog" in configuration else None
    if args.deployed:
        if args.schema_directory is not None:
            parser.error("--deployed cannot be combined with a schema-directory override")
        consumers = (args.ingest_container, args.bridge_container) + (("db-writer", "field_enricher_processor") if catalog else ())
        deployment = DockerDeployment(consumers, sudo=args.docker_sudo, network=args.network if catalog else None)
        def provider():
            result, descriptor = deployment.snapshot(configuration)
            if catalog:
                storage_ready(result["storage_id"])
                result = catalog.augment(result)
            return result, descriptor
    else:
        if args.docker_sudo:
            parser.error("--docker-sudo requires --deployed")
        directory = args.schema_directory
        if directory is None:
            directory = Path(Runfiles.Create().Rlocation("_main/lib/codegen/telemetry/artemis_schema_contract.json")).parent
        selected = profile(directory, configuration)
        provider = lambda: selected
    if catalog and not args.deployed:
        parser.error("automatic catalogs require the actual deployed consumers")
    stopped = threading.Event()
    def refresh():
        while not stopped.is_set():
            try:
                catalog.sync()
            except Exception:
                logging.exception("Release discovery failed; retaining the verified catalog")
            stopped.wait(60)
    if catalog:
        threading.Thread(target=refresh, daemon=True).start()
    with create_server(provider, (args.listen, args.port)) as server:
        try:
            server.serve_forever()
        finally:
            stopped.set()


if __name__ == "__main__":
    main()
