"""Mic board framing (PROTOCOL.md section 3): 'V' | type | uint32 LE length | payload."""
from __future__ import annotations

import struct

MAGIC = 0x56  # 'V'
HEADER = struct.Struct("<BcI")
MAX_PAYLOAD = 1 << 20


def encode_frame(ftype: str, payload: bytes | str = b"") -> bytes:
    if isinstance(payload, str):
        payload = payload.encode()
    return HEADER.pack(MAGIC, ftype.encode(), len(payload)) + payload


class FrameReader:
    """Incremental decoder. feed() bytes, get back a list of (type, payload)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        self._buf.extend(data)
        frames = []
        while True:
            if not self._buf:
                break
            if self._buf[0] != MAGIC:  # resync on garbage
                idx = self._buf.find(bytes([MAGIC]))
                del self._buf[: idx if idx >= 0 else len(self._buf)]
                continue
            if len(self._buf) < HEADER.size:
                break
            _, ftype, length = HEADER.unpack_from(self._buf)
            if length > MAX_PAYLOAD:
                del self._buf[0]
                continue
            if len(self._buf) < HEADER.size + length:
                break
            payload = bytes(self._buf[HEADER.size : HEADER.size + length])
            del self._buf[: HEADER.size + length]
            frames.append((ftype.decode("latin-1"), payload))
        return frames
