"""网关入口：HTTP 端口、TCP 端口均可通过环境变量配置。"""

from __future__ import annotations

import os
import signal
import threading

from .gateway import Gateway
from .server import ThreadingHTTPServerWithGateway, ThreadingTCPServer


def _env(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def main() -> None:
    http_host = _env("HTTP_HOST", "0.0.0.0")
    http_port = int(_env("HTTP_PORT", "8080"))
    tcp_host = _env("TCP_HOST", "0.0.0.0")
    tcp_port = int(_env("TCP_PORT", "9100"))

    gateway = Gateway()
    tcp_server = ThreadingTCPServer((tcp_host, tcp_port), gateway)
    http_server = ThreadingHTTPServerWithGateway((http_host, http_port), gateway)

    threading.Thread(target=tcp_server.serve_forever, daemon=True).start()
    threading.Thread(target=http_server.serve_forever, daemon=True).start()

    print(f"telemetry gateway listening: tcp={tcp_host}:{tcp_port} http={http_host}:{http_port}", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    tcp_server.shutdown()
    http_server.shutdown()


if __name__ == "__main__":
    main()
