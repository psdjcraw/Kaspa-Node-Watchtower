#!/usr/bin/env python3
"""Serve the watchtower Prometheus textfile over HTTP."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_METRICS_PATH = Path(__file__).resolve().parent / "state" / "watchtower.prom"
HOST = os.environ.get("KASPA_WATCHTOWER_EXPORTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("KASPA_WATCHTOWER_EXPORTER_PORT", "9660"))
METRICS_PATH = Path(os.environ.get("KASPA_WATCHTOWER_METRICS_PATH", str(DEFAULT_METRICS_PATH)))


class MetricsHandler(BaseHTTPRequestHandler):
    server_version = "KaspaWatchtowerPrometheus/1.0"

    def do_GET(self) -> None:
        if self.path == "/-/healthy":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path != "/metrics":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"not found\n")
            return
        if not METRICS_PATH.exists():
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"metrics file missing: {METRICS_PATH}\n".encode("utf-8"))
            return

        data = METRICS_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), MetricsHandler)
    print(f"kaspa watchtower metrics exporter listening on {HOST}:{PORT}/metrics", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
