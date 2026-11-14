import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest

from analysis.query_client import GatewayClient, QueryError


class ClientTest(unittest.TestCase):
    def test_paging_exact_identity_conflict_and_stream_gap(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                if body.get("page_token") == "changed":
                    self.send_response(409)
                    payload = {"error": {"code": "query_changed", "message": "Restart query"}}
                else:
                    self.send_response(200)
                    payload = {"data": [{"sample_time_ns": "1794484800000000001", "value": "18446744073709551615"}], "next_page_token": None if body.get("page_token") else "second"}
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b': heartbeat\n\ndata: {"type":"data","page":{"data":[]}}\n\ndata: {"type":"gap","message":"Coverage lost"}\n\n')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with GatewayClient(f"http://127.0.0.1:{server.server_port}", "test") as client:
                rows = client.query({"signals": ["controls.apps1_travel"]})
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["value"], "18446744073709551615")
                self.assertEqual(requests[1]["page_token"], "second")
                with self.assertRaises(QueryError) as error:
                    client.query({"page_token": "changed"})
                self.assertEqual(error.exception.code, "query_changed")
                self.assertEqual([e["type"] for e in client.stream({})], ["data", "gap"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
