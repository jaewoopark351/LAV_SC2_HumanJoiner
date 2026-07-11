#20260628_kpopmodder: Added a narrow remote-human start listener for Host-driven LAN lobby launches.
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

from sc2_api_probe import wait_for_sc2_api_ping
from sc2_join_launcher import build_launch_plan, human_client_port, human_slot_room, launch_sc2
from sc2_lan_port_relay import (
    SC2LanPortRelayManager,
    derive_multiplayer_ports,
    derive_second_player_client_ports,
    resolve_lan_bind_host,
)
from sc2_lan_discovery_client import (
    DEFAULT_HUMAN_CLIENT_PORT,
    DEFAULT_REMOTE_START_PORT,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LAV_REMOTE_HUMAN_START_ACK_PROTOCOL,
    LAV_REMOTE_HUMAN_START_PROTOCOL,
    LAV_REMOTE_HUMAN_START_VERSION,
    LanRoom,
    select_room_connect_host,
)


DEFAULT_SC2_API_READY_TIMEOUT_SEC = 60.0


class RemoteHumanStartServer:
    def __init__(
        self,
        *,
        bind_host: str = "",
        port: int = DEFAULT_REMOTE_START_PORT,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bind_host = str(bind_host or "")
        self.port = _valid_port(port, DEFAULT_REMOTE_START_PORT)
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._room: LanRoom | None = None
        self._sc2_executable: Path | None = None
        self._last_process = None
        self._last_status: dict[str, Any] = {"running": False}
        self._multiplayer_relay = SC2LanPortRelayManager(
            log_callback=lambda message: self.logger.info("SC2 multiplayer relay: %s", message)
        )
        self._loopback_relay = SC2LanPortRelayManager(
            log_callback=lambda message: self.logger.info("SC2 loopback relay: %s", message)
        )

    def start(self, room: LanRoom, sc2_executable: Path | None) -> dict[str, Any]:
        with self._lock:
            self._room = room
            self._sc2_executable = sc2_executable
            if self.is_running():
                self._last_status = self.status()
                return self._last_status
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._listen_loop,
                name="RemoteHumanStartServer.listen",
                daemon=True,
            )
            self._thread.start()
            self._last_status = self.status()
            return self._last_status

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        self._sock = None
        self._multiplayer_relay.stop()
        self._loopback_relay.stop()

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict[str, Any]:
        process = self._last_process
        room = self._room
        return {
            "running": self.is_running(),
            "bind_host": self.bind_host or "0.0.0.0",
            "port": self.port,
            "room_id": room.room_id if room is not None else "",
            "sc2_executable": str(self._sc2_executable or ""),
            "last_pid": getattr(process, "pid", None) if process is not None else None,
            "last_process_running": bool(process is not None and process.poll() is None),
            "multiplayer_relay": self._multiplayer_relay.status(),
            "multiplayer_loopback_relay": self._loopback_relay.status(),
            "last_status": dict(self._last_status),
        }

    def prepare_sc2(
        self,
        room: LanRoom,
        sc2_executable: Path | None,
        *,
        ready_timeout_sec: float = DEFAULT_SC2_API_READY_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        with self._lock:
            self._room = room
            self._sc2_executable = sc2_executable
            process = self._last_process

        if sc2_executable is None or not sc2_executable.is_file():
            response = _ack(False, error="sc2_executable_missing", room_id=room.room_id)
            self._last_status = response
            return response

        response = self._ensure_sc2_api_ready(
            room,
            sc2_executable,
            process,
            ready_timeout_sec=ready_timeout_sec,
            peer="local_prepare",
        )
        response = self._ensure_multiplayer_relay_ready(
            room,
            response,
            peer_host=select_room_connect_host(room),
        )
        self._last_status = response
        self.logger.info("SC2 prepare request handled; response=%s", response)
        return response

    def _listen_loop(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.bind_host, self.port))
                sock.listen(5)
                sock.settimeout(0.5)
                self._sock = sock
                self.logger.info(
                    "Remote human start listener ready; bind_host=%s port=%s",
                    self.bind_host or "0.0.0.0",
                    self.port,
                )
                while not self._stop.is_set():
                    try:
                        conn, address = sock.accept()
                    except socket.timeout:
                        continue
                    except OSError as exc:
                        if not self._stop.is_set():
                            self.logger.warning("Remote start listener accept failed: %s", exc)
                        break
                    with conn:
                        self._handle_connection(conn, address)
        except OSError as exc:
            self._last_status = {"running": False, "ok": False, "error": str(exc)}
            self.logger.warning("Remote human start listener failed: %s", exc)
        finally:
            self._sock = None

    def _handle_connection(self, conn: socket.socket, address: tuple[str, int]) -> None:
        response = self._handle_request(_recv_json_object(conn), address)
        data = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            conn.sendall(data)
        except OSError as exc:
            self.logger.warning("Remote start ack failed: %s", exc)

    def _handle_request(self, payload: dict[str, Any], address: tuple[str, int]) -> dict[str, Any]:
        if payload.get("protocol") != LAV_REMOTE_HUMAN_START_PROTOCOL:
            return _ack(False, error="unsupported_protocol")
        if payload.get("version") != LAV_REMOTE_HUMAN_START_VERSION:
            return _ack(False, error="unsupported_version")

        with self._lock:
            room = self._room
            sc2_executable = self._sc2_executable
            process = self._last_process

        if room is None:
            return _ack(False, error="remote_start_room_missing")
        requested_room_id = str(payload.get("room_id") or "").strip()
        if requested_room_id and requested_room_id != room.room_id:
            return _ack(False, error="room_id_mismatch", room_id=room.room_id)
        if sc2_executable is None or not sc2_executable.is_file():
            return _ack(False, error="sc2_executable_missing", room_id=room.room_id)

        response = self._ensure_sc2_api_ready(
            room,
            sc2_executable,
            process,
            ready_timeout_sec=_request_timeout(payload, DEFAULT_SC2_API_READY_TIMEOUT_SEC),
            peer=f"{address[0]}:{address[1]}",
        )
        response = self._ensure_multiplayer_relay_ready(
            room,
            response,
            peer_host=address[0],
        )
        self._last_status = response
        self.logger.info("Remote start request handled; response=%s", response)
        return response

    def _ensure_sc2_api_ready(
        self,
        room: LanRoom,
        sc2_executable: Path,
        process: Any,
        *,
        ready_timeout_sec: float,
        peer: str,
    ) -> dict[str, Any]:
        port = human_client_port(room)
        if process is not None and process.poll() is None:
            probe = wait_for_sc2_api_ping(
                timeout_sec=1.0,
                per_attempt_timeout_sec=0.5,
                sleep_sec=0.1,
                port=port,
            )
            if probe.ok:
                return _ack(
                    True,
                    room_id=room.room_id,
                    pid=process.pid,
                    human_client_port=port,
                    message="already_ready",
                    api_ready=True,
                    api_ready_attempts=probe.attempts,
                )
        else:
            process = None

        if process is None:
            try:
                plan = build_launch_plan(human_slot_room(room), sc2_executable)
                process = launch_sc2(plan)
            except Exception as exc:
                return _ack(False, error=str(exc), room_id=room.room_id)

            with self._lock:
                self._last_process = process

        ready_timeout = max(0.1, float(ready_timeout_sec or DEFAULT_SC2_API_READY_TIMEOUT_SEC))
        ready_started = time.monotonic()
        port_ready = _wait_for_port(port, timeout_sec=ready_timeout, process=process)
        remaining_timeout = max(0.1, ready_timeout - (time.monotonic() - ready_started))
        probe = wait_for_sc2_api_ping(timeout_sec=remaining_timeout, port=port) if port_ready else None
        ready = bool(probe is not None and probe.ok)
        return _ack(
            ready,
            error="" if ready else "human_sc2_api_ping_not_ready",
            room_id=room.room_id,
            pid=getattr(process, "pid", None),
            human_client_port=port,
            peer=peer,
            port_ready=port_ready,
            api_ready=ready,
            api_ready_attempts=getattr(probe, "attempts", 0),
            api_ready_error=getattr(probe, "error", "") if probe is not None else "human_sc2_api_port_not_ready",
        )

    def _ensure_multiplayer_relay_ready(
        self,
        room: LanRoom,
        response: dict[str, Any],
        *,
        peer_host: str,
    ) -> dict[str, Any]:
        if not response.get("ok"):
            return response
        if not bool(room.multiplayer_relay_enabled):
            self._loopback_relay.stop()
            response["multiplayer_relay"] = {
                "ok": True,
                "running": False,
                "skipped": "multiplayer_relay_disabled",
            }
            response["loopback_relay"] = {
                "ok": True,
                "running": False,
                "skipped": "multiplayer_relay_disabled",
            }
            return response

        peer_host = str(peer_host or select_room_connect_host(room) or "").strip()
        ports = derive_multiplayer_ports(room.start_port, room.multiplayer_relay_ports)
        loopback_ports = derive_second_player_client_ports(room.start_port)
        bind_host = resolve_lan_bind_host(
            room.multiplayer_relay_bind_host,
            peer_host=peer_host,
        )
        relay_result = self._multiplayer_relay.start(
            bind_host=bind_host,
            ports=ports,
            target_host="127.0.0.1",
            enable_tcp=True,
            enable_udp=True,
        )
        relay_result["selected_peer_host"] = peer_host
        relay_result["selected_bind_host"] = bind_host
        relay_result["selected_ports"] = ports

        if peer_host:
            loopback_result = self._loopback_relay.start(
                bind_host="127.0.0.1",
                ports=loopback_ports,
                target_host=peer_host,
                enable_tcp=True,
                enable_udp=True,
            )
        else:
            self._loopback_relay.stop()
            loopback_result = {
                "ok": False,
                "running": False,
                "error": "host_peer_missing",
                "config": {
                    "bind_host": "127.0.0.1",
                    "target_host": "",
                    "ports": loopback_ports,
                    "enable_tcp": True,
                    "enable_udp": True,
                },
            }
        loopback_result["selected_peer_host"] = peer_host
        loopback_result["selected_bind_host"] = "127.0.0.1"
        loopback_result["selected_ports"] = loopback_ports

        response["multiplayer_relay"] = relay_result
        response["loopback_relay"] = loopback_result
        if not relay_result.get("ok", False) or not loopback_result.get("ok", False):
            response["ok"] = False
            errors = [
                str(item.get("error") or "")
                for item in (relay_result, loopback_result)
                if isinstance(item, dict) and item.get("error")
            ]
            response["error"] = "; ".join(errors) or "multiplayer_relay_failed"
        return response


def _recv_json_object(conn: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    conn.settimeout(5.0)
    while True:
        chunk = conn.recv(8192)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _wait_for_port(port: int, *, timeout_sec: float, process: Any = None) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_sec or 0.1))
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _ack(ok: bool, **values: Any) -> dict[str, Any]:
    response = {
        "protocol": LAV_REMOTE_HUMAN_START_ACK_PROTOCOL,
        "version": LAV_REMOTE_HUMAN_START_VERSION,
        "ok": bool(ok),
        "timestamp": time.time(),
    }
    response.update(values)
    return response


def _request_timeout(payload: dict[str, Any], default: float) -> float:
    try:
        value = float(payload.get("ready_timeout_sec", default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _valid_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port <= 65535 else default
