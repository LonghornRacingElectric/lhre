"""Local editing UI for the CAN spec (lib/spec).

Run with `bazel run //tools/spec:editor`, then open the printed URL. The
backend is deliberately thin: it imports the same loader, validator, and
canonical serializer the build uses (no parallel logic) and exposes them
over three JSON endpoints; all editing intelligence lives in the page.

    GET  /api/spec      -> {files: {path: SpecFile-as-JSON}, digest}
    POST /api/validate  -> {errors: [...], warnings: [...]}
    POST /api/save      -> validates, then writes canonical textproto.
                           Refuses if the on-disk files changed since the
                           client loaded them (digest mismatch) or if
                           validation fails.

Plain http.server instead of a web framework: a localhost, single-user
form tool doesn't need one, and every extra pip wheel is another set of
long runfiles paths for Windows to trip over.
"""

import argparse
import hashlib
import json
import os
import pathlib
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.protobuf import json_format
from runfiles import Runfiles

from lib.spec import canonical, loader, validator
from lib.spec.proto import can_spec_pb2

SPEC_DIR = "lib/spec"
INDEX_RLOCATION = "_main/tools/spec/editor_index.html"


def load_files(root):
    """Reads every spec file. Returns {relative posix path: file text}."""
    texts = {}
    for path in sorted((root / SPEC_DIR).rglob("*.textproto")):
        rel = path.relative_to(root).as_posix()
        texts[rel] = path.read_text(encoding="utf-8")
    return texts


def digest(texts):
    h = hashlib.sha256()
    for rel in sorted(texts):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(texts[rel].encode())
        h.update(b"\0")
    return h.hexdigest()


def spec_payload(root):
    """The GET /api/spec response body."""
    texts = load_files(root)
    files = {}
    for rel, text in texts.items():
        files[rel] = json_format.MessageToDict(
            loader.parse_file(text, rel),
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=True,
        )
    return {"files": files, "digest": digest(texts)}


def parse_payload(files_json):
    """JSON files map -> {rel: SpecFile}. Raises ValueError on bad paths
    or malformed messages."""
    protos = {}
    for rel, file_json in files_json.items():
        clean = pathlib.PurePosixPath(rel)
        if clean.is_absolute() or ".." in clean.parts or not rel.startswith(SPEC_DIR + "/") or not rel.endswith(".textproto"):
            raise ValueError(f"refusing path outside {SPEC_DIR}/: {rel}")
        try:
            protos[rel] = json_format.ParseDict(file_json, can_spec_pb2.SpecFile())
        except json_format.ParseError as err:
            raise ValueError(f"{rel}: {err}") from err
    return protos


def validate_files(protos):
    return validator.validate(loader.Spec(protos))


def save_files(root, files_json, expected_digest):
    """Validates and writes the spec. Returns (written_paths, response).
    Raises ValueError with a user-facing message on any refusal."""
    protos = parse_payload(files_json)
    if digest(load_files(root)) != expected_digest:
        raise ValueError(
            "spec files changed on disk since this page loaded "
            "(another editor, `fmt`, or a git operation) — reload first")
    errors, warnings = validate_files(protos)
    if errors:
        raise ValueError("validation failed:\n" + "\n".join(errors))
    written = []
    for rel, proto in protos.items():
        text = canonical.canonicalize(proto)
        path = root / rel
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(rel)
    return written, warnings


class _Handler(BaseHTTPRequestHandler):
    root = None  # set by serve()
    index_html = None

    def _reply(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._reply(200, self.index_html, "text/html; charset=utf-8")
            elif self.path == "/api/spec":
                self._reply(200, spec_payload(self.root))
            else:
                self._reply(404, {"error": "not found"})
        except Exception as err:  # surface everything to the page
            self._reply(500, {"error": str(err)})

    def do_POST(self):
        try:
            if self.path == "/api/validate":
                errors, warnings = validate_files(parse_payload(self._body()["files"]))
                self._reply(200, {"errors": errors, "warnings": warnings})
            elif self.path == "/api/save":
                body = self._body()
                try:
                    written, warnings = save_files(self.root, body["files"], body["digest"])
                    self._reply(200, {"written": written, "warnings": warnings})
                except ValueError as err:
                    self._reply(409, {"error": str(err)})
            else:
                self._reply(404, {"error": "not found"})
        except Exception as err:
            self._reply(500, {"error": str(err)})

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass


def serve(root, port, open_browser=True):
    r = Runfiles.Create()
    _Handler.root = root
    _Handler.index_html = pathlib.Path(r.Rlocation(INDEX_RLOCATION)).read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"CAN spec editor: {url}  (Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace:
        print("run via: bazel run //tools/spec:editor", file=sys.stderr)
        return 1
    return serve(pathlib.Path(workspace), args.port, open_browser=not args.no_open) or 0


if __name__ == "__main__":
    sys.exit(main())
