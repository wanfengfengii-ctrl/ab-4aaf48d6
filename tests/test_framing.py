import struct
import zlib

from app.framing import (
    HEADER_FIXED,
    MAX_FRAME,
    MIN_FRAME,
    SYNC,
    Frame,
    Reassembler,
    encode_frame,
)


def feed_all(r: Reassembler, data: bytes, chunk: int = 1):
    out = []
    for i in range(0, len(data), chunk):
        out.extend(r.feed(data[i : i + chunk]))
    return out


def test_single_frame_simple():
    f = encode_frame(3, 7, b"hello")
    frames = Reassembler().feed(f)
    assert frames == [Frame(3, 7, b"hello")]


def test_byte_at_a_time_reassembly():
    f = encode_frame(5, 9, b"x" * 1024)
    assert feed_all(Reassembler(), f, chunk=1) == [Frame(5, 9, b"x" * 1024)]


def test_coalesced_multiple_frames():
    stream = b"".join(encode_frame(1, i, bytes([i]) * 3) for i in range(10))
    assert feed_all(Reassembler(), stream, chunk=7) == [
        Frame(1, i, bytes([i]) * 3) for i in range(10)
    ]


def test_garbage_prefix_is_skipped():
    stream = b"\x00\x1a\x00garbage" + encode_frame(2, 0, b"ok")
    frames = feed_all(Reassembler(), stream, chunk=4)
    assert frames == [Frame(2, 0, b"ok")]


def test_bad_crc_is_dropped_and_resyncs():
    good1 = encode_frame(1, 0, b"first")
    bad = bytearray(encode_frame(1, 1, b"broken"))
    bad[-1] ^= 0xFF  # 翻转 CRC 末字节
    good2 = encode_frame(1, 2, b"second")
    frames = feed_all(Reassembler(), bytes(bad) + good2 + good1, chunk=3)
    # 坏帧被丢弃；前后好帧无论顺序都应被找到
    assert sorted(frames, key=lambda f: f.sequence) == [
        Frame(1, 0, b"first"),
        Frame(1, 2, b"second"),
    ]


def test_sync_word_inside_bad_payload_still_resyncs():
    # 构造一个 CRC 错误、但载荷内部藏有同步字+好帧的候选
    hidden = encode_frame(4, 42, b"hidden")
    body = HEADER_FIXED.pack(4, 100, len(hidden)) + hidden
    bad = SYNC + body + b"\xde\xad\xbe\xef"  # 伪造的错误 CRC
    frames = feed_all(Reassembler(), bad, chunk=5)
    assert frames == [Frame(4, 42, b"hidden")]


def test_invalid_length_zero_is_skipped():
    body = HEADER_FIXED.pack(1, 0, 0)
    bad = SYNC + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    good = encode_frame(1, 0, b"valid")
    frames = feed_all(Reassembler(), bad + good, chunk=2)
    assert frames == [Frame(1, 0, b"valid")]


def test_invalid_length_too_large_is_skipped():
    body = HEADER_FIXED.pack(1, 0, 1025)
    bad = SYNC + body + b"\x00" * 4
    good = encode_frame(1, 0, b"valid")
    frames = feed_all(Reassembler(), bad + good, chunk=6)
    assert frames == [Frame(1, 0, b"valid")]


def test_partial_frame_waits_for_more_bytes():
    r = Reassembler()
    f = encode_frame(9, 1, b"partial")
    assert r.feed(f[:5]) == []
    assert r.feed(f[5:10]) == []
    assert r.feed(f[10:]) == [Frame(9, 1, b"partial")]
    assert r.feed(b"") == []  # 无残留


def test_truncated_tail_does_not_block_next_connection_data():
    r = Reassembler()
    f = encode_frame(9, 2, b"later")
    # 收到半帧（合法长度，等待其余字节）
    head = f[: len(SYNC) + 3]
    assert r.feed(head) == []
    # 同一逻辑连接后续数据到达（残包可跨 feed 拼接）
    assert r.feed(f[len(SYNC) + 3 :]) == [Frame(9, 2, b"later")]


def test_sync_prefix_in_tail_is_retained():
    # 尾部恰好是同步字的前 3 个字节，下一帧在下一次 feed 到达
    f = encode_frame(1, 0, b"a")
    assert feed_all(Reassembler(), SYNC[:3] + f, chunk=1) == [Frame(1, 0, b"a")]


def test_frame_size_bounds():
    assert MIN_FRAME == 4 + 7 + 1 + 4
    assert MAX_FRAME == 4 + 7 + 1024 + 4
