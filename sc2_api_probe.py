#20260711_kpopmodder: Minimal dependency-free SC2 API websocket ping probe for remote-human readiness.
from __future__ import annotations

import base64
import os
import socket
import struct
import time
from dataclasses import dataclass


SC2_API_PATH = "/sc2api"
SC2_API_PING_REQUEST = b"\x9a\x01\x00"
SC2_API_PING_FIELD_KEY = b"\x9a\x01"


@dataclass(frozen=True)
class SC2ApiPingResult:
    ok: bool
    attempts: int = 0
    error: str = ""


def wait_for_sc2_api_ping(
    host: str = "127.0.0.1",
    port: int = 5679,
    *,
    timeout_sec: float = 30.0,
    per_attempt_timeout_sec: float = 2.0,
    sleep_sec: float = 0.5,
) -> SC2ApiPingResult:
    deadline = time.monotonic() + max(0.1, float(timeout_sec or 0.1))
    attempts = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempts += 1
        result = ping_sc2_api_once(host, port, timeout_sec=per_attempt_timeout_sec)
        if result.ok:
            return SC2ApiPingResult(ok=True, attempts=attempts)
        last_error = result.error
        time.sleep(max(0.05, float(sleep_sec or 0.05)))
    return SC2ApiPingResult(ok=False, attempts=attempts, error=last_error or "sc2_api_ping_timeout")


def ping_sc2_api_once(host: str = "127.0.0.1", port: int = 5679, *, timeout_sec: float = 2.0) -> SC2ApiPingResult:
    try:
        with socket.create_connection((str(host or "127.0.0.1"), int(port)), timeout=float(timeout_sec or 2.0)) as sock:
            sock.settimeout(float(timeout_sec or 2.0))
            _websocket_handshake(sock, str(host or "127.0.0.1"), int(port))
            _send_websocket_binary(sock, SC2_API_PING_REQUEST)
            opcode, payload = _recv_websocket_frame(sock)
    except Exception as exc:
        return SC2ApiPingResult(ok=False, attempts=1, error=str(exc))
    if opcode != 0x2:
        return SC2ApiPingResult(ok=False, attempts=1, error=f"unexpected_websocket_opcode:{opcode}")
    if SC2_API_PING_FIELD_KEY not in payload:
        return SC2ApiPingResult(ok=False, attempts=1, error=f"ping_response_missing:{payload.hex()[:80]}")
    return SC2ApiPingResult(ok=True, attempts=1)


def _websocket_handshake(sock: socket.socket, host: str, port: int) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {SC2_API_PATH} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")
    sock.sendall(request)
    response = _recv_until(sock, b"\r\n\r\n", limit=8192)
    header = response.decode("iso-8859-1", errors="replace").lower()
    if " 101 " not in header.split("\r\n", 1)[0]:
        raise RuntimeError(f"websocket_handshake_failed:{response[:120]!r}")
    if "upgrade: websocket" not in header:
        raise RuntimeError("websocket_upgrade_missing")


def _send_websocket_binary(sock: socket.socket, payload: bytes) -> None:
    # Client-to-server websocket frames must be masked.
    mask = os.urandom(4)
    header = bytearray([0x82])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _recv_websocket_frame(sock: socket.socket) -> tuple[int, bytes]:
    first = _recv_exact(sock, 2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode == 0x8:
        raise RuntimeError("websocket_closed")
    return opcode, payload


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("socket_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_until(sock: socket.socket, marker: bytes, *, limit: int) -> bytes:
    data = bytearray()
    while marker not in data:
        chunk = sock.recv(1024)
        if not chunk:
            raise RuntimeError("socket_closed")
        data.extend(chunk)
        if len(data) > limit:
            raise RuntimeError("response_too_large")
    return bytes(data)
