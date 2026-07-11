from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


DEFAULT_DISCOVERY_PORT = 47624
DEFAULT_JOIN_PORT = DEFAULT_DISCOVERY_PORT + 1
DEFAULT_HUMAN_CLIENT_PORT = 5679
DEFAULT_REMOTE_START_PORT = DEFAULT_DISCOVERY_PORT + 2
DEFAULT_MAP_DOWNLOAD_PORT = DEFAULT_DISCOVERY_PORT + 3
DEFAULT_SCAN_SECONDS = 10.0
DEFAULT_SOCKET_TIMEOUT_SECONDS = 0.5
DEFAULT_ROOM_TTL_SECONDS = 10.0
DEFAULT_MAX_PACKET_EVENTS = 20
LAV_LAN_ROOM_PROTOCOL = "lav.sc2.lan_room"
LAV_LAN_ROOM_VERSION = 1
LAV_LOBBY_JOIN_PROTOCOL = "lav.sc2.lobby_join"
LAV_LOBBY_JOIN_ACK_PROTOCOL = "lav.sc2.lobby_join_ack"
LAV_LOBBY_JOIN_VERSION = 1
LAV_REMOTE_HUMAN_START_PROTOCOL = "lav.sc2.remote_human_start"
LAV_REMOTE_HUMAN_START_ACK_PROTOCOL = "lav.sc2.remote_human_start_ack"
LAV_REMOTE_HUMAN_START_VERSION = 1


@dataclass(frozen=True)
class RoomKey:
    room_id: str
    source_id: str


@dataclass
class LanRoom:
    protocol: str
    version: int
    source_id: str
    room_id: str
    room_name: str = ""
    host_name: str = ""
    player_name: str = ""
    mode: str = ""
    preferred_bot: str = ""
    preferred_map: str = ""
    proxy_host: str = ""
    proxy_ports: list[int] = field(default_factory=list)
    start_port: int | None = None
    join_port: int | None = None
    human_client_port: int | None = None
    remote_start_port: int | None = None
    map_file_name: str = ""
    map_size: int | None = None
    map_sha256: str = ""
    map_download_port: int | None = None
    map_download_path: str = ""
    room_state: str = ""
    timestamp: float = 0.0
    expires_sec: float = DEFAULT_ROOM_TTL_SECONDS
    last_seen: float = 0.0
    sender_ip: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> RoomKey:
        return RoomKey(room_id=self.room_id, source_id=self.source_id)

    @property
    def expires_at(self) -> float:
        return self.last_seen + self.expires_sec

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


class LanRoomPayloadError(ValueError):
    """Raised when a UDP payload is not a valid LAV LAN room broadcast."""


@dataclass
class LobbyJoinResult:
    ok: bool
    target_host: str
    target_port: int
    room_id: str
    client_id: str
    ack: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class LanScanPacketEvent:
    sender_ip: str
    sender_port: int | str
    byte_count: int
    raw_payload: str
    parse_result: str = "pending"


@dataclass
class LanScanDiagnostics:
    bind_host: str = ""
    port: int = 0
    duration_sec: float = 0.0
    started_at: str = ""
    ended_at: str = ""
    bind_succeeded: bool = False
    bind_error: str = ""
    packets_received: int = 0
    parsed_rooms: int = 0
    rooms_returned: int = 0
    parse_failures: int = 0
    parse_failure_reasons: dict[str, int] = field(default_factory=dict)
    sender_counts: dict[str, int] = field(default_factory=dict)
    packet_events: list[LanScanPacketEvent] = field(default_factory=list)
    max_packet_events: int = DEFAULT_MAX_PACKET_EVENTS

    def start(self, *, bind_host: str, port: int, duration_sec: float) -> None:
        self.bind_host = bind_host
        self.port = port
        self.duration_sec = duration_sec
        self.started_at = _wall_time_text()

    def finish(self, *, rooms_returned: int) -> None:
        self.rooms_returned = rooms_returned
        self.ended_at = _wall_time_text()

    def record_bind_success(self) -> None:
        self.bind_succeeded = True
        self.bind_error = ""

    def record_bind_error(self, error: str) -> None:
        self.bind_succeeded = False
        self.bind_error = error

    def record_packet(self, sender_ip: str, sender_port: int | str, payload: bytes) -> int | None:
        self.packets_received += 1
        sender = sender_ip or "<unknown>"
        self.sender_counts[sender] = self.sender_counts.get(sender, 0) + 1
        if len(self.packet_events) >= self.max_packet_events:
            return None
        self.packet_events.append(
            LanScanPacketEvent(
                sender_ip=sender,
                sender_port=sender_port,
                byte_count=len(payload),
                raw_payload=_payload_for_log(payload, limit=500),
            )
        )
        return len(self.packet_events) - 1

    def record_parse_success(self, event_index: int | None = None, room: LanRoom | None = None) -> None:
        self.parsed_rooms += 1
        if event_index is not None and event_index < len(self.packet_events):
            detail = "OK"
            if room is not None:
                detail = (
                    f"OK room_id={room.room_id} source_id={room.source_id} "
                    f"proxy_host={room.proxy_host} proxy_ports={room.proxy_ports}"
                )
            self.packet_events[event_index].parse_result = detail

    def record_parse_failure(self, reason: str, event_index: int | None = None) -> None:
        self.parse_failures += 1
        self.parse_failure_reasons[reason] = self.parse_failure_reasons.get(reason, 0) + 1
        if event_index is not None and event_index < len(self.packet_events):
            self.packet_events[event_index].parse_result = f"FAILED reason={reason}"

    def log_summary(self, logger: logging.Logger, prefix: str) -> None:
        logger.info(
            "%s diagnostics; bind_host=%s port=%s duration_sec=%s started_at=%s ended_at=%s "
            "bind_succeeded=%s bind_error=%s packets_received=%s parsed_rooms=%s rooms_returned=%s "
            "parse_failures=%s parse_failure_reasons=%s sender_counts=%s",
            prefix,
            self.bind_host or "0.0.0.0",
            self.port,
            self.duration_sec,
            self.started_at,
            self.ended_at,
            self.bind_succeeded,
            self.bind_error,
            self.packets_received,
            self.parsed_rooms,
            self.rooms_returned,
            self.parse_failures,
            self.parse_failure_reasons,
            self.sender_counts,
        )


def parse_lav_lan_room_payload(
    payload: bytes | str,
    *,
    received_at: float | None = None,
    sender_ip: str = "",
) -> LanRoom:
    data = _load_json_object(payload)

    if data.get("protocol") != LAV_LAN_ROOM_PROTOCOL:
        raise LanRoomPayloadError("unsupported protocol")
    if data.get("version") != LAV_LAN_ROOM_VERSION:
        raise LanRoomPayloadError("unsupported version")

    source_id = _required_string(data, "source_id")
    room_id = _required_string(data, "room_id")
    now = time.monotonic() if received_at is None else received_at

    return LanRoom(
        protocol=LAV_LAN_ROOM_PROTOCOL,
        version=LAV_LAN_ROOM_VERSION,
        source_id=source_id,
        room_id=room_id,
        room_name=_optional_string(data, "room_name"),
        host_name=_optional_string(data, "host_name"),
        player_name=_optional_string(data, "player_name"),
        mode=_optional_string(data, "mode"),
        preferred_bot=_optional_string(data, "preferred_bot"),
        preferred_map=_optional_string(data, "preferred_map"),
        proxy_host=_optional_string(data, "proxy_host"),
        proxy_ports=_integer_list(data.get("proxy_ports", []), "proxy_ports"),
        start_port=_optional_integer(data, "start_port"),
        join_port=_optional_integer(data, "join_port"),
        human_client_port=_optional_integer(data, "human_client_port"),
        remote_start_port=_optional_integer(data, "remote_start_port"),
        map_file_name=_optional_string(data, "map_file_name"),
        map_size=_optional_integer(data, "map_size"),
        map_sha256=_optional_string(data, "map_sha256"),
        map_download_port=_optional_integer(data, "map_download_port"),
        map_download_path=_optional_string(data, "map_download_path"),
        room_state=_optional_string(data, "room_state"),
        timestamp=_optional_float(data, "timestamp", 0.0),
        expires_sec=_positive_float(data.get("expires_sec", DEFAULT_ROOM_TTL_SECONDS), "expires_sec"),
        last_seen=now,
        sender_ip=sender_ip,
        raw=dict(data),
    )


class LanRoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[RoomKey, LanRoom] = {}

    def update(self, room: LanRoom) -> None:
        self._rooms[room.key] = room

    def remove_expired(self, now: float) -> None:
        expired_keys = [key for key, room in self._rooms.items() if room.is_expired(now)]
        for key in expired_keys:
            del self._rooms[key]

    def rooms(self, *, now: float | None = None) -> list[LanRoom]:
        if now is not None:
            self.remove_expired(now)
        return sorted(
            self._rooms.values(),
            key=lambda room: (room.room_name.lower(), room.host_name.lower(), room.room_id, room.source_id),
        )


class LanDiscoveryClient:
    def __init__(
        self,
        *,
        bind_host: str = "",
        port: int = DEFAULT_DISCOVERY_PORT,
        timeout_sec: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bind_host = bind_host
        self.port = port
        self.timeout_sec = timeout_sec
        self.clock = clock

    def scan(
        self,
        *,
        duration_sec: float = DEFAULT_SCAN_SECONDS,
        on_room: Callable[[LanRoom], None] | None = None,
        diagnostics: LanScanDiagnostics | None = None,
        debug_logger: logging.Logger | None = None,
        log_payloads: bool = False,
    ) -> list[LanRoom]:
        if diagnostics is None:
            diagnostics = LanScanDiagnostics()
        diagnostics.start(bind_host=self.bind_host, port=self.port, duration_sec=duration_sec)
        registry = LanRoomRegistry()
        deadline = self.clock() + duration_sec

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                _enable_address_reuse(sock)
                try:
                    sock.bind((self.bind_host, self.port))
                except OSError as exc:
                    diagnostics.record_bind_error(str(exc))
                    raise
                diagnostics.record_bind_success()
                if debug_logger is not None:
                    debug_logger.info(
                        "UDP scan bind succeeded; bind_host=%s port=%s",
                        self.bind_host or "0.0.0.0",
                        self.port,
                    )
                sock.settimeout(self.timeout_sec)

                while self.clock() < deadline:
                    now = self.clock()
                    registry.remove_expired(now)
                    try:
                        payload, sender = sock.recvfrom(65535)
                    except socket.timeout:
                        continue

                    sender_ip = sender[0] if sender else ""
                    sender_port = sender[1] if sender and len(sender) > 1 else ""
                    event_index = diagnostics.record_packet(sender_ip, sender_port, payload)
                    if debug_logger is not None and log_payloads:
                        debug_logger.info(
                            "UDP packet received; sender_ip=%s sender_port=%s bytes=%s raw_payload=%s",
                            sender_ip or "<unknown>",
                            sender_port,
                            len(payload),
                            _payload_for_log(payload),
                        )
                    try:
                        room = parse_lav_lan_room_payload(payload, received_at=now, sender_ip=sender_ip)
                    except LanRoomPayloadError as exc:
                        diagnostics.record_parse_failure(str(exc), event_index)
                        if debug_logger is not None and log_payloads:
                            debug_logger.info(
                                "UDP payload parse failed; sender_ip=%s reason=%s",
                                sender_ip or "<unknown>",
                                exc,
                        )
                        continue

                    diagnostics.record_parse_success(event_index, room)
                    if debug_logger is not None and log_payloads:
                        debug_logger.info(
                            "UDP payload parsed; sender_ip=%s room_id=%s source_id=%s proxy_host=%s proxy_ports=%s",
                            sender_ip or "<unknown>",
                            room.room_id,
                            room.source_id,
                            room.proxy_host,
                            room.proxy_ports,
                        )
                    registry.update(room)
                    if on_room is not None:
                        on_room(room)

                registry.remove_expired(self.clock())
                rooms = registry.rooms()
                diagnostics.finish(rooms_returned=len(rooms))
                return rooms
        finally:
            if not diagnostics.ended_at:
                diagnostics.finish(rooms_returned=diagnostics.rooms_returned)


def send_lobby_join(
    room: LanRoom,
    *,
    player_name: str = "Human",
    timeout_sec: float = 2.0,
    client_id: str | None = None,
    target_host: str | None = None,
    target_port: int | None = None,
) -> LobbyJoinResult:
    host = select_lobby_join_host(room, target_host=target_host)
    port = int(target_port or room.join_port or DEFAULT_JOIN_PORT)
    request_client_id = str(client_id or uuid.uuid4().hex)
    if not host:
        return LobbyJoinResult(
            ok=False,
            target_host="",
            target_port=port,
            room_id=room.room_id,
            client_id=request_client_id,
            error="join target host is empty",
        )
    payload = {
        "protocol": LAV_LOBBY_JOIN_PROTOCOL,
        "version": LAV_LOBBY_JOIN_VERSION,
        "room_id": room.room_id,
        "source_id": room.source_id,
        "client_id": request_client_id,
        "player_name": str(player_name or "Human").strip() or "Human",
        "host_name": socket.gethostname(),
        "proxy_host": room.proxy_host,
        "proxy_ports": list(room.proxy_ports),
        "human_client_port": room.human_client_port or DEFAULT_HUMAN_CLIENT_PORT,
        "remote_start_port": room.remote_start_port or DEFAULT_REMOTE_START_PORT,
        "timestamp": time.time(),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(max(0.1, float(timeout_sec or 2.0)))
            sock.sendto(data, (host, port))
            ack_data, _ = sock.recvfrom(8192)
    except OSError as exc:
        return LobbyJoinResult(
            ok=False,
            target_host=host,
            target_port=port,
            room_id=room.room_id,
            client_id=request_client_id,
            error=str(exc),
        )

    try:
        ack = _load_json_object(ack_data)
    except LanRoomPayloadError as exc:
        return LobbyJoinResult(
            ok=False,
            target_host=host,
            target_port=port,
            room_id=room.room_id,
            client_id=request_client_id,
            error=f"invalid join ack: {exc}",
        )
    if ack.get("protocol") != LAV_LOBBY_JOIN_ACK_PROTOCOL:
        return LobbyJoinResult(
            ok=False,
            target_host=host,
            target_port=port,
            room_id=room.room_id,
            client_id=request_client_id,
            ack=ack,
            error="unsupported join ack protocol",
        )
    return LobbyJoinResult(
        ok=bool(ack.get("ok")),
        target_host=host,
        target_port=port,
        room_id=room.room_id,
        client_id=request_client_id,
        ack=ack,
        error="" if bool(ack.get("ok")) else str(ack.get("message") or "join rejected"),
    )


def select_lobby_join_host(room: LanRoom, *, target_host: str | None = None) -> str:
    if target_host is not None:
        return _clean_connect_host(target_host)
    return select_room_connect_host(room)


def select_room_connect_host(room: LanRoom) -> str:
    sender_ip = _clean_connect_host(room.sender_ip)
    proxy_host = _clean_connect_host(room.proxy_host)
    host_name = _clean_connect_host(room.host_name)

    if _is_loopback_host(sender_ip):
        return sender_ip
    if proxy_host and not _is_loopback_host(proxy_host):
        return proxy_host
    if sender_ip:
        return sender_ip
    if proxy_host:
        return proxy_host
    return host_name


def _clean_connect_host(value: object) -> str:
    text = str(value or "").strip()
    if _is_unspecified_host(text):
        return ""
    return text


def _is_unspecified_host(value: str) -> bool:
    text = str(value or "").strip().lower()
    return not text or text in {"0.0.0.0", "::", "[::]"}


def _is_loopback_host(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text == "localhost" or text.startswith("127.") or text in {"::1", "[::1]"}


def _enable_address_reuse(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass


def _wall_time_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _payload_for_log(payload: bytes, *, limit: int = 2000) -> str:
    text = payload.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _load_json_object(payload: bytes | str) -> dict[str, Any]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LanRoomPayloadError("payload is not utf-8") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LanRoomPayloadError("payload is not json") from exc

    if not isinstance(data, dict):
        raise LanRoomPayloadError("payload must be a json object")
    return data


def _required_string(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise LanRoomPayloadError(f"{name} must be a non-empty string")
    return value


def _optional_string(data: dict[str, Any], name: str) -> str:
    value = data.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LanRoomPayloadError(f"{name} must be a string")
    return value


def _optional_integer(data: dict[str, Any], name: str) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LanRoomPayloadError(f"{name} must be an integer")
    return value


def _optional_float(data: dict[str, Any], name: str, default: float) -> float:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LanRoomPayloadError(f"{name} must be a number")
    return float(value)


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LanRoomPayloadError(f"{name} must be a number")
    value = float(value)
    if value <= 0:
        raise LanRoomPayloadError(f"{name} must be positive")
    return value


def _integer_list(value: Any, name: str) -> list[int]:
    if not isinstance(value, list):
        raise LanRoomPayloadError(f"{name} must be a list")

    ports: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise LanRoomPayloadError(f"{name} must contain integers")
        ports.append(item)
    return ports
