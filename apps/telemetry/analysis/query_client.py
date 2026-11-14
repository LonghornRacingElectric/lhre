"""Small HTTP/SSE client for Artemis telemetry analysis."""
import json
import os
import time

import requests


class QueryError(RuntimeError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


class GatewayClient:
    def __init__(self, url=None, token=None):
        self.url = (url or os.getenv("QUERY_GATEWAY_URL", "http://127.0.0.1:8090")).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + (token or os.environ["QUERY_API_TOKEN"])})

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, self.url + path, timeout=(5, 35), **kwargs)
        if not response.ok:
            try:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                raise QueryError(response.status_code, error.get("code", "request_failed"), error.get("message", response.reason))
            finally:
                response.close()
        return response

    def discover(self):
        with self._request("GET", "/v1/signals") as response:
            return response.json()

    def sessions(self):
        with self._request("GET", "/v1/sessions") as response:
            return response.json()["sessions"]

    def status(self):
        with self._request("GET", "/v1/stats") as response:
            return response.json()

    def pages(self, body):
        request = {"car": "artemis", "page_size": 10000, **body}
        while True:
            with self._request("POST", "/v1/query", json=request) as response:
                page = response.json()
            yield page
            if not page["next_page_token"]:
                return
            request["page_token"] = page["next_page_token"]

    def query(self, body):
        rows = []
        for page in self.pages(body):
            rows.extend(page["data"])
            if len(rows) > 1_000_000:
                raise ValueError("Analysis result exceeds one million observations; narrow the range")
        return rows

    def stream(self, body, timeout=None):
        return self._events("/v1/stream", {"query": json.dumps({"car": "artemis", **body})}, timeout)

    def topic(self, topic):
        return self._events("/v1/topics/" + requests.utils.quote(topic, safe=""), {})

    def _events(self, path, params, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self._request("GET", path, params=params, stream=True) as response:
            parts = []
            for raw in response.iter_lines(chunk_size=None):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Gateway stream deadline exceeded")
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    parts.append(line[5:].lstrip(" "))
                elif not line and parts:
                    yield json.loads("\n".join(parts))
                    parts.clear()
