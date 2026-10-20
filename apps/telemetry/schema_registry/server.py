"""Read-only server schema authority for the separate BEVO updater."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re

from runfiles import Runfiles

from lib.telemetry.contract import Schema, canonical_json


def profile(schema_directory, configuration):
    schema_directory = Path(schema_directory)
    schema = Schema((schema_directory / "schema_contract.json").read_bytes(), (schema_directory / "schema_descriptor.pb").read_bytes())
    releases = configuration["releases"]
    if not isinstance(releases, list) or len(releases) > 32 or any(not isinstance(reference, str) or not re.fullmatch(r"ghcr\.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}", reference) for reference in releases):
        raise ValueError("releases must be an ordered list of at most 32 digest-pinned GHCR images")
    return {
        "format_version": 1, "schema": schema.manifest,
        "schema_descriptor_sha256": hashlib.sha256(schema.descriptor_set).hexdigest(),
        "releases": releases,
    }, schema.descriptor_set


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=os.getenv("TELEMETRY_SCHEMA_RELEASES"), required=not os.getenv("TELEMETRY_SCHEMA_RELEASES"))
    parser.add_argument("--schema-directory", type=Path, default=os.getenv("TELEMETRY_SERVER_SCHEMA_DIR"))
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8085)
    args = parser.parse_args()
    directory = args.schema_directory
    if directory is None:
        directory = Path(Runfiles.Create().Rlocation("_main/lib/codegen/telemetry/schema_contract.json")).parent
    configuration, descriptor = profile(directory, json.loads(args.config.read_text()))
    payload = canonical_json(configuration).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/profile.json":
                content, mime = payload, "application/json"
            elif self.path == "/schema_descriptor.pb":
                content, mime = descriptor, "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
