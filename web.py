"""
Entry point for free cloud hosts (Render, Koyeb, Railway) that expect a
web service listening on $PORT. The bot polls Telegram on the main thread;
a tiny HTTP server answers health checks so the host keeps us alive.

Start command on the host:  python web.py
"""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import bot


class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"signal bot alive\n")

    def log_message(self, *args):  # silence per-request noise
        return


def serve_health() -> None:
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=serve_health, daemon=True).start()
    bot.main()
