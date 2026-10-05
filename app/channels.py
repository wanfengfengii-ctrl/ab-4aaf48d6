"""信道状态机：乱序暂存、去重、冲突/越界判定与连续载荷摘要。"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field

from .framing import MAX_WINDOW, Frame

STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"


@dataclass
class Channel:
    channel_id: int
    next_sequence: int = 0
    # 所有“见过”的序号（含已交付与窗口内暂存）
    seen: set[int] = field(default_factory=set)
    # sequence -> payload，仅暂存领先 next_sequence 且在窗口内的帧
    pending: dict[int, bytes] = field(default_factory=dict)
    # 已交付序号 -> 载荷 SHA-256，用于任意延迟重传的同载荷/冲突判定
    accepted_hash: dict[int, bytes] = field(default_factory=dict)
    payload_bytes: int = 0
    digest: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    status: str = STATUS_ACTIVE
    failure_reason: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _fail(self, reason: str) -> None:
        self.status = STATUS_FAILED
        self.failure_reason = reason
        self.pending.clear()

    def ingest(self, frame: Frame) -> None:
        """处理一帧。失败信道上的后续帧一律忽略；协议违规永久标记 failed。"""
        with self.lock:
            if self.status == STATUS_FAILED:
                return
            seq = frame.sequence

            # 已交付序号的重传：载荷指纹一致则忽略，不一致即内容冲突。
            if seq < self.next_sequence:
                accepted = self.accepted_hash.get(seq)
                if accepted is None or accepted != hashlib.sha256(frame.payload).digest():
                    self._fail(
                        f"content conflict at sequence {seq}: retransmitted payload "
                        "differs from the payload already accepted at that sequence"
                    )
                return

            # 窗口越界：领先 next_sequence 超过 MAX_WINDOW，永久失败。
            if seq > self.next_sequence + MAX_WINDOW:
                self._fail(
                    f"sequence {seq} out of window: next sequence is "
                    f"{self.next_sequence}, maximum lead is {MAX_WINDOW}"
                )
                return

            # 窗口内同序号重传：同载荷忽略，内容冲突永久失败。
            if seq in self.pending:
                if self.pending[seq] != frame.payload:
                    self._fail(
                        f"content conflict at sequence {seq}: two frames with the "
                        "same sequence number carry different payloads"
                    )
                return

            self.seen.add(seq)
            self.pending[seq] = frame.payload
            self._drain()

    def _drain(self) -> None:
        """把自 next_sequence 起连续到达的帧按序并入摘要。"""
        while self.next_sequence in self.pending:
            payload = self.pending.pop(self.next_sequence)
            self.accepted_hash[self.next_sequence] = hashlib.sha256(payload).digest()
            self.digest.update(payload)
            self.payload_bytes += len(payload)
            self.next_sequence += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "channelId": self.channel_id,
                "status": self.status,
                "nextSequence": self.next_sequence,
                "seenSequences": len(self.seen),
                "contiguousPayloadBytes": self.payload_bytes,
                "sha256": self.digest.hexdigest(),
                "failureReason": self.failure_reason,
            }
