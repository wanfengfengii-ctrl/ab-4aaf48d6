"""TCP 接入与 HTTP 查询服务（仅依赖 Python 标准库）。"""

from __future__ import annotations

import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .framing import Reassembler
from .gateway import Gateway


class TCPFrameHandler(socketserver.BaseRequestHandler):
    """每个 TCP 连接独立重组器；解析出的帧进同一套信道状态。"""

    def handle(self) -> None:
        reassembler = Reassembler()
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    break
                for frame in reassembler.feed(data):
                    self.server.gateway.dispatch(frame)
        except (ConnectionError, OSError):
            pass


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], gateway: Gateway) -> None:
        super().__init__(address, TCPFrameHandler)
        self.gateway = gateway


class _HTTPHandler(BaseHTTPRequestHandler):
    server_version = "LeoGateway/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        prefix = "/api/channels/"
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if path.startswith(prefix):
            raw = path[len(prefix) :]
            if raw.isdigit() and raw != "":
                channel = self.server.gateway.get(int(raw))
                if channel is None:
                    self._send_json(404, {"error": "channel not found", "channelId": int(raw)})
                    return
                self._send_json(200, channel.snapshot())
                return
        self._send_json(404, {"error": "not found", "path": path})

    def log_message(self, *_: Any) -> None:  # 静默常规访问日志
        return


class ThreadingHTTPServerWithGateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], gateway: Gateway) -> None:
        super().__init__(address, _HTTPHandler)
        self.gateway = gateway
