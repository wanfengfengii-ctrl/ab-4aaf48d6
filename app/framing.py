"""帧定义与重组器：在任意拆包/粘包/坏帧夹杂的字节流中寻找同步字并切出帧。

帧布局（大端）：
    同步字 1A CF FC 1D（4 字节）
    信道号（1 字节）
    序号（4 字节，uint32）
    载荷长度（2 字节，uint16）
    载荷（length 字节，1..1024）
    CRC32（4 字节，覆盖信道号..载荷）
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SYNC = b"\x1a\xcf\xfc\x1d"
HEADER_FIXED = struct.Struct(">B I H")  # channel, sequence, length
MIN_PAYLOAD = 1
MAX_PAYLOAD = 1024
# 同步字(4) + 信道/序号/长度(7) + 载荷(1..1024) + CRC(4)
MIN_FRAME = len(SYNC) + HEADER_FIXED.size + MIN_PAYLOAD + 4
MAX_FRAME = len(SYNC) + HEADER_FIXED.size + MAX_PAYLOAD + 4
MAX_WINDOW = 31  # 可暂存领先 next_sequence 的帧数


@dataclass(frozen=True)
class Frame:
    channel: int
    sequence: int
    payload: bytes


def encode_frame(channel: int, sequence: int, payload: bytes) -> bytes:
    """按线路格式编码一帧（供测试/客户端使用）。"""
    body = HEADER_FIXED.pack(channel & 0xFF, sequence & 0xFFFFFFFF, len(payload)) + payload
    return SYNC + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


class Reassembler:
    """跨多次 feed 保留残包的流式重组器。

    非法长度或 CRC 错误的候选帧只丢弃候选本身，然后从其后第一个字节
    重新寻找同步字（同步字可能出现在候选载荷内部），从而保证不会漏掉
    恰好粘在坏帧后面的好帧。
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        frames: list[Frame] = []
        buf = self._buf
        pos = 0  # 候选同步字起点
        # limit：pos 之后至少要有一帧最小长度才可能解析
        while pos + MIN_FRAME <= len(buf):
            if buf[pos : pos + 4] != SYNC:
                pos += 1
                continue
            body_start = pos + 4
            channel, sequence, length = HEADER_FIXED.unpack_from(buf, body_start)
            frame_end = body_start + HEADER_FIXED.size + length + 4
            if length < MIN_PAYLOAD or length > MAX_PAYLOAD or frame_end > len(buf):
                # 非法长度：此同步字候选作废，从下一字节继续找。
                # 长度合法但整帧尚未到齐：等待更多数据（length<=1024 有界）。
                if MIN_PAYLOAD <= length <= MAX_PAYLOAD:
                    break
                pos += 1
                continue
            payload = bytes(buf[body_start + HEADER_FIXED.size : body_start + HEADER_FIXED.size + length])
            crc_rx = struct.unpack_from(">I", buf, frame_end - 4)[0]
            crc_calc = zlib.crc32(bytes(buf[body_start : body_start + HEADER_FIXED.size + length])) & 0xFFFFFFFF
            if crc_rx != crc_calc:
                # CRC 错误：丢弃候选，从同步字之后重新搜索（载荷内可能藏有同步字）。
                pos += 1
                continue
            frames.append(Frame(channel, sequence, payload))
            pos = frame_end
        # 尾部不足一帧：只保留可能成为跨包同步字（或其前缀）的起点，
        # 其余无同步字的垃圾直接丢弃，避免残包无限增长。
        keep = len(buf)
        for i in range(pos, len(buf)):
            tail = bytes(buf[i:])
            if SYNC.startswith(tail) or tail.startswith(SYNC):
                keep = i
                break
        del buf[:keep]
        return frames
