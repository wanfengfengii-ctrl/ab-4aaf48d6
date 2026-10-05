"""真实 TCP 写入 + HTTP 查询冒烟。

由 docker compose 的 verify 一次性服务对正在运行的 gateway 执行：
- 跨多个 TCP 连接乱序发送（含单字节拆包、坏帧粘连）；
- 校验连续载荷字节数与 SHA-256 和本地计算一致；
- 校验同序号内容冲突的信道稳定 failed 且带明确原因；
- 校验未知信道 404。
任何断言失败均以非零退出码退出。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.framing import encode_frame  # noqa: E402

HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
TCP_PORT = int(os.environ.get("GATEWAY_TCP_PORT", "9100"))
HTTP_PORT = int(os.environ.get("GATEWAY_HTTP_PORT", "8080"))

GOOD_CHANNEL = int(os.environ.get("SMOKE_GOOD_CHANNEL", "200"))
BAD_CHANNEL = int(os.environ.get("SMOKE_BAD_CHANNEL", "201"))
MISSING_CHANNEL = int(os.environ.get("SMOKE_MISSING_CHANNEL", "250"))


def send_raw(data: bytes, byte_by_byte: bool = False) -> None:
    with socket.create_connection((HOST, TCP_PORT), timeout=10) as s:
        if byte_by_byte:
            for b in data:
                s.sendall(bytes([b]))
        else:
            s.sendall(data)


def http_get(path: str):
    try:
        with urllib.request.urlopen(f"http://{HOST}:{HTTP_PORT}{path}", timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_for(channel: int, predicate, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        status, last = http_get(f"/api/channels/{channel}")
        if status == 200 and predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"channel {channel} did not reach expected state in time: {last}")


def main() -> int:
    payloads = [(f"smoke-payload-{i}-".encode() * (i + 7)) for i in range(6)]
    frames = [encode_frame(GOOD_CHANNEL, i, payloads[i]) for i in range(6)]
    expect = hashlib.sha256(b"".join(payloads)).hexdigest()

    # 连接 A：单字节发送奇数序号（极端拆包 + 乱序）
    send_raw(b"".join(frames[i] for i in (1, 3, 5)), byte_by_byte=True)
    # 连接 B：CRC 坏帧与垃圾 + 偶数序号正确帧粘连
    bad = bytearray(encode_frame(GOOD_CHANNEL, 99, b"corrupt"))
    bad[-2] ^= 0xAA
    send_raw(bytes(bad) + b"\x00\xff\x1a\xcf" + b"".join(frames[i] for i in (0, 2, 4)))
    # 连接 C：重传 seq 5 的同载荷帧，必须被忽略
    send_raw(frames[5])

    snap = wait_for(GOOD_CHANNEL, lambda s: s["nextSequence"] == 6)
    assert snap["status"] == "active", snap
    assert snap["seenSequences"] == 6, snap
    assert snap["contiguousPayloadBytes"] == sum(len(p) for p in payloads), snap
    assert snap["sha256"] == expect, (snap["sha256"], expect)
    assert snap["failureReason"] is None, snap
    print(f"[smoke] good channel digest OK: {expect}", flush=True)

    # 同序号内容冲突 → 永久 failed，原因明确，且状态稳定
    send_raw(encode_frame(BAD_CHANNEL, 0, b"first-version"))
    wait_for(BAD_CHANNEL, lambda s: s["nextSequence"] == 1)
    send_raw(encode_frame(BAD_CHANNEL, 0, b"second-version-CONFLICT"))
    failed = wait_for(BAD_CHANNEL, lambda s: s["status"] == "failed")
    assert "conflict" in failed["failureReason"], failed
    send_raw(encode_frame(BAD_CHANNEL, 1, b"after-failure"))
    time.sleep(0.5)
    _, again = http_get(f"/api/channels/{BAD_CHANNEL}")
    assert again == failed, (again, failed)
    print(f"[smoke] conflict channel failed stably: {failed['failureReason']}", flush=True)

    # 未知信道 404
    status, body = http_get(f"/api/channels/{MISSING_CHANNEL}")
    assert status == 404, (status, body)
    print("[smoke] unknown channel 404 OK", flush=True)

    print("[smoke] ALL SMOKE CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — 冒烟脚本需把任何失败转为退出码
        print(f"[smoke] FAILED: {exc!r}", file=sys.stderr, flush=True)
        sys.exit(1)
