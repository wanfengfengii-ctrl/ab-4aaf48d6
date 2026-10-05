"""网关：跨连接共享的信道注册表与帧分发。"""

from __future__ import annotations

import threading

from .channels import Channel
from .framing import Frame


class Gateway:
    def __init__(self) -> None:
        self._channels: dict[int, Channel] = {}
        self._lock = threading.Lock()

    def dispatch(self, frame: Frame) -> None:
        with self._lock:
            channel = self._channels.get(frame.channel)
            if channel is None:
                channel = Channel(frame.channel)
                self._channels[frame.channel] = channel
        channel.ingest(frame)

    def get(self, channel_id: int) -> Channel | None:
        return self._channels.get(channel_id)
