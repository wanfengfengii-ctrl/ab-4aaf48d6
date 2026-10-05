"""真实 socket 集成测试：多连接、乱序、粘坏帧、单字节发送。"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing

import pytest

from app.framing import encode_frame
from app.gateway import Gateway
from app.server import ThreadingHTTPServerWithGateway, ThreadingTCPServer


@pytest.fixture()
def services():
    gateway = Gateway()
    tcp = ThreadingTCPServer(("127.0.0.1", 0), gateway)
    http = ThreadingHTTPServerWithGateway(("127.0.0.1", 0), gateway)
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield tcp.server_address[1], http.server_address[1], gateway
    tcp.shutdown()
    http.shutdown()


def send_byte_by_byte(port: int, data: bytes) -> None:
    with socket.create_connection(("127.0.0.1", port)) as s:
        for b in data:
            s.sendall(bytes([b]))
            time.sleep(0)  # 让出调度，制造极端拆包


def send_all(port: int, data: bytes) -> None:
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(data)


def query(http_port: int, channel: int):
    # 404 返回 (404, body) 而不抛异常：帧异步处理时信道可能尚未建立
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/api/channels/{channel}") as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def query_status(http_port: int, channel: int) -> int:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{http_port}/api/channels/{channel}")
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def expected_digest(parts: list[bytes]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()


def test_cross_connection_reorder_bytewise_and_coalesced_bad(services):
    tcp_port, http_port, _ = services
    channel = 21
    parts = [f"seq-{i}-payload".encode() * (i + 1) for i in range(8)]
    frames = [encode_frame(channel, i, parts[i]) for i in range(8)]

    # 连接 A：单字节发送奇数序号
    t1 = threading.Thread(target=send_byte_by_byte, args=(tcp_port, b"".join(frames[i] for i in (1, 3, 5, 7))))
    # 连接 B：先粘一个 CRC 坏帧 + 垃圾，再单字节发送偶数序号
    bad = bytearray(encode_frame(channel, 6, b"will-corrupt"))
    bad[-1] ^= 0xFF
    t2 = threading.Thread(
        target=send_byte_by_byte,
        args=(tcp_port, bytes(bad) + b"\x99" * 13 + b"".join(frames[i] for i in (0, 2, 4))),
    )
    # 连接 C：乱序发送 6（正确版本）
    t3 = threading.Thread(target=send_all, args=(tcp_port, frames[6]))
    t1.start(); t2.start(); t3.start()
    t1.join(); t2.join(); t3.join()

    # 等待异步处理（帧随单字节到达，最后一帧在连接关闭后解析）
    deadline = time.time() + 5
    while time.time() < deadline:
        _, snap = query(http_port, channel)
        if snap["nextSequence"] == 8:
            break
        time.sleep(0.02)
    _, snap = query(http_port, channel)
    assert snap["status"] == "active"
    assert snap["nextSequence"] == 8
    assert snap["seenSequences"] == 8
    assert snap["contiguousPayloadBytes"] == sum(len(p) for p in parts)
    assert snap["sha256"] == expected_digest(parts)
    assert snap["failureReason"] is None


def test_unknown_channel_404_and_health(services):
    tcp_port, http_port, _ = services
    assert query_status(http_port, 9999) == 404
    with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/healthz") as r:
        assert r.status == 200
        assert json.loads(r.read())["status"] == "ok"


def test_conflict_channel_reports_failure_over_http(services):
    tcp_port, http_port, _ = services
    channel = 77
    send_all(tcp_port, encode_frame(channel, 0, b"alpha"))
    send_all(tcp_port, encode_frame(channel, 0, b"ALPHA-CONFLICT"))
    deadline = time.time() + 5
    snap = None
    while time.time() < deadline:
        _, snap = query(http_port, channel)
        if snap["status"] == "failed":
            break
        time.sleep(0.02)
    assert snap["status"] == "failed"
    assert "conflict" in snap["failureReason"]
    # 失败稳定：后续任何数据都不改变状态
    before = json.dumps(snap, sort_keys=True)
    send_all(tcp_port, encode_frame(channel, 1, b"anything"))
    time.sleep(0.2)
    _, snap2 = query(http_port, channel)
    assert json.dumps(snap2, sort_keys=True) == before


def test_window_violation_over_tcp(services):
    tcp_port, http_port, _ = services
    channel = 88
    send_all(tcp_port, encode_frame(channel, 32, b"jump"))
    deadline = time.time() + 5
    snap = None
    while time.time() < deadline:
        status, snap = query(http_port, channel)
        if status == 200 and snap["status"] == "failed":
            break
        time.sleep(0.02)
    assert snap is not None and snap["status"] == "failed", snap
    assert "window" in snap["failureReason"]


def test_multiple_channels_are_independent(services):
    tcp_port, http_port, _ = services
    send_all(tcp_port, encode_frame(1, 0, b"ch1") + encode_frame(2, 0, b"ch2-data"))
    deadline = time.time() + 5
    while time.time() < deadline:
        _, s1 = query(http_port, 1)
        if s1["nextSequence"] == 1:
            break
        time.sleep(0.02)
    _, s1 = query(http_port, 1)
    _, s2 = query(http_port, 2)
    assert s1["sha256"] == hashlib.sha256(b"ch1").hexdigest()
    assert s2["sha256"] == hashlib.sha256(b"ch2-data").hexdigest()
