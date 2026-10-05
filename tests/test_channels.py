import hashlib

from app.channels import STATUS_ACTIVE, STATUS_FAILED, Channel
from app.framing import Frame, encode_frame
from app.framing import MAX_WINDOW


def f(seq: int, payload: bytes, channel: int = 1) -> Frame:
    return Frame(channel, seq, payload)


def digest_of(payloads: list[bytes]) -> str:
    h = hashlib.sha256()
    for p in payloads:
        h.update(p)
    return h.hexdigest()


def test_in_order_delivery():
    ch = Channel(1)
    for i in range(5):
        ch.ingest(f(i, f"p{i}".encode()))
    snap = ch.snapshot()
    assert snap["status"] == STATUS_ACTIVE
    assert snap["nextSequence"] == 5
    assert snap["seenSequences"] == 5
    assert snap["contiguousPayloadBytes"] == sum(len(f"p{i}".encode()) for i in range(5))
    assert snap["sha256"] == digest_of([f"p{i}".encode() for i in range(5)])
    assert snap["failureReason"] is None


def test_out_of_order_within_window_drains():
    ch = Channel(2)
    for seq in (3, 1, 2, 0):
        ch.ingest(f(seq, f"payload-{seq}".encode()))
    snap = ch.snapshot()
    assert snap["nextSequence"] == 4
    assert snap["sha256"] == digest_of([f"payload-{i}".encode() for i in range(4)])
    # 之后到达的 4 正常接续
    ch.ingest(f(4, b"next"))
    assert ch.snapshot()["nextSequence"] == 5


def test_gap_holds_then_drains():
    ch = Channel(3)
    ch.ingest(f(1, b"one"))
    snap = ch.snapshot()
    assert snap["nextSequence"] == 0
    assert snap["contiguousPayloadBytes"] == 0
    assert snap["seenSequences"] == 1
    ch.ingest(f(0, b"zero"))
    snap = ch.snapshot()
    assert snap["nextSequence"] == 2
    assert snap["sha256"] == digest_of([b"zero", b"one"])


def test_duplicate_same_payload_ignored():
    ch = Channel(4)
    ch.ingest(f(0, b"a"))
    ch.ingest(f(0, b"a"))  # 立即重传
    ch.ingest(f(1, b"b"))
    ch.ingest(f(0, b"a"))  # 交付后的延迟重传
    snap = ch.snapshot()
    assert snap["status"] == STATUS_ACTIVE
    assert snap["nextSequence"] == 2
    assert snap["seenSequences"] == 2
    assert snap["sha256"] == digest_of([b"a", b"b"])


def test_conflict_pending_marks_failed():
    ch = Channel(5)
    ch.ingest(f(2, b"newer"))
    ch.ingest(f(2, b"DIFFERENT"))
    snap = ch.snapshot()
    assert snap["status"] == STATUS_FAILED
    assert "conflict" in snap["failureReason"]
    assert "2" in snap["failureReason"]


def test_duplicate_pending_same_payload_ignored():
    ch = Channel(40)
    ch.ingest(f(1, b"held"))
    ch.ingest(f(1, b"held"))  # 缺口未补、仍在窗口内暂存时重传
    ch.ingest(f(0, b"zero"))
    snap = ch.snapshot()
    assert snap["status"] == STATUS_ACTIVE
    assert snap["nextSequence"] == 2
    assert snap["seenSequences"] == 2
    assert snap["sha256"] == digest_of([b"zero", b"held"])


def test_conflict_after_accepted_marks_failed():
    ch = Channel(6)
    ch.ingest(f(0, b"original"))
    ch.ingest(f(1, b"x"))
    ch.ingest(f(0, b"TAMPERED"))
    snap = ch.snapshot()
    assert snap["status"] == STATUS_FAILED
    assert "conflict" in snap["failureReason"]


def test_window_overrun_marks_failed():
    ch = Channel(7)
    ch.ingest(f(MAX_WINDOW, b"edge-ok"))  # 领先恰好 31：允许
    assert ch.snapshot()["status"] == STATUS_ACTIVE
    ch.ingest(f(MAX_WINDOW + 2, b"too-far"))  # 领先 32：越界
    snap = ch.snapshot()
    assert snap["status"] == STATUS_FAILED
    assert "out of window" in snap["failureReason"]


def test_failed_channel_is_permanent_and_ignores_input():
    ch = Channel(8)
    ch.ingest(f(0, b"x"))
    ch.ingest(f(0, b"y"))
    assert ch.snapshot()["status"] == STATUS_FAILED
    before = ch.snapshot()
    ch.ingest(f(1, b"z"))
    ch.ingest(f(2, b"z"))
    after = ch.snapshot()
    assert after == before  # 永久失败，状态冻结


def test_full_window_fill_recovers():
    ch = Channel(9)
    for seq in range(1, MAX_WINDOW + 1):
        ch.ingest(f(seq, bytes([seq])))
    assert ch.snapshot()["nextSequence"] == 0
    ch.ingest(f(0, b"start"))
    snap = ch.snapshot()
    assert snap["nextSequence"] == MAX_WINDOW + 1
    assert snap["sha256"] == digest_of([b"start"] + [bytes([i]) for i in range(1, MAX_WINDOW + 1)])


def test_encode_frame_roundtrip_helper():
    raw = encode_frame(10, 55, b"abc")
    from app.framing import Reassembler

    frames = Reassembler().feed(raw)
    assert frames == [f(55, b"abc", channel=10)]
