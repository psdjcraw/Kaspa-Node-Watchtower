#!/usr/bin/env python3
"""Serve a lightweight KAS market chart dashboard backed by Prometheus."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "market"
PROMETHEUS_URL = "http://127.0.0.1:9090"


class Handler(BaseHTTPRequestHandler):
    server_version = "KaspaMarketDashboard/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/api/query_range":
            self._proxy_prometheus("/api/v1/query_range", parsed.query)
            return
        if parsed.path == "/api/query":
            self._proxy_prometheus("/api/v1/query", parsed.query)
            return
        if parsed.path == "/api/metric_names":
            self._send_market_metric_names()
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "time": time.time()})
            return
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"not found\n")

    def _proxy_prometheus(self, path: str, query: str) -> None:
        url = f"{PROMETHEUS_URL}{path}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=12) as response:
                body = response.read()
                status = response.status
        except Exception as exc:  # pragma: no cover - operational path
            self._send_json({"status": "error", "error": str(exc)}, status=502)
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_market_metric_names(self) -> None:
        url = f"{PROMETHEUS_URL}/api/v1/label/__name__/values"
        try:
            with urllib.request.urlopen(url, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - operational path
            self._send_json({"status": "error", "error": str(exc)}, status=502)
            return
        names = [
            name
            for name in data.get("data", [])
            if name.startswith("kaspa_watchtower_market_")
        ]
        self._send_json({"status": "success", "data": sorted(names)})

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"KAS market dashboard: http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
