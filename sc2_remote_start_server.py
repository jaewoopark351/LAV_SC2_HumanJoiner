#20260628_kpopmodder: Added a narrow remote-human start listener for Host-driven LAN lobby launches.
from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from sc2_api_probe import wait_for_sc2_api_ping
from sc2_join_launcher import build_launch_plan, human_client_port, human_slot_room, launch_sc2
from sc2_lan_port_relay import (
    SC2LanPortRelayManager,
    SC2UdpPortPairRelayManager,
    derive_first_player_client_ports,
    derive_first_player_server_ports,
    derive_multiplayer_ports,
    derive_second_player_server_ports,
    normalize_lan_port_layout,
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
        self._last_native_joiner_process = None
        self._last_native_joiner_status: dict[str, Any] = {"running": False}
        self._last_status: dict[str, Any] = {"running": False}
        self._multiplayer_relay = SC2LanPortRelayManager(
            log_callback=lambda message: self.logger.info("SC2 multiplayer relay: %s", message)
        )
        self._loopback_relay = SC2LanPortRelayManager(
            log_callback=lambda message: self.logger.info("SC2 loopback relay: %s", message)
        )
        self._udp_pair_relay = SC2UdpPortPairRelayManager(
            log_callback=lambda message: self.logger.info("SC2 UDP pair relay: %s", message)
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
        self._udp_pair_relay.stop()

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
            "native_joiner": dict(self._last_native_joiner_status),
            "multiplayer_relay": self._multiplayer_relay.status(),
            "multiplayer_loopback_relay": self._loopback_relay.status(),
            "multiplayer_udp_pair_relay": self._udp_pair_relay.status(),
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
        command = str(payload.get("command") or "start_sc2").strip().lower()
        if command == "start_native_joiner":
            response = self._ensure_native_joiner_ready(room, response, payload)
        elif command not in {"", "start_sc2", "prepare_sc2"}:
            response = _ack(False, error="unsupported_command", command=command, room_id=room.room_id)
        self._last_status = response
        self.logger.info("Remote start request handled; command=%s response=%s", command, response)
        return response

    def _ensure_native_joiner_ready(
        self,
        room: LanRoom,
        response: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not response.get("ok"):
            return response
        native_joiner = payload.get("native_joiner")
        native_joiner = native_joiner if isinstance(native_joiner, dict) else {}
        executable, candidates = _resolve_native_joiner_executable(native_joiner, Path(__file__).resolve().parent)
        if executable is None:
            response["ok"] = False
            response["error"] = "native_joiner_executable_missing"
            response["native_joiner"] = {"ok": False, "candidates": candidates}
            self._last_native_joiner_status = dict(response["native_joiner"])
            return response

        process = self._last_native_joiner_process
        if process is not None and process.poll() is None:
            status = {
                "ok": True,
                "running": True,
                "pid": process.pid,
                "message": "already_running",
                "executable": str(executable),
            }
            response["native_joiner"] = status
            self._last_native_joiner_status = dict(status)
            return response

        command = [str(executable), *_native_joiner_args(native_joiner, room)]
        log_path = _native_joiner_log_path(Path(__file__).resolve().parent)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("ab", buffering=0) as log_file:
                log_file.write(("\n--- LavLanRemoteJoiner launch ---\n" + " ".join(command) + "\n").encode("utf-8", errors="replace"))
                process = subprocess.Popen(
                    command,
                    cwd=str(executable.parent),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=_native_joiner_creation_flags(),
                )
        except Exception as exc:
            response["ok"] = False
            response["error"] = f"native_joiner_launch_failed: {exc}"
            response["native_joiner"] = {
                "ok": False,
                "executable": str(executable),
                "command": command,
                "log_path": str(log_path),
                "error": str(exc),
            }
            self._last_native_joiner_status = dict(response["native_joiner"])
            return response

        with self._lock:
            self._last_native_joiner_process = process
        status = {
            "ok": True,
            "running": True,
            "pid": process.pid,
            "executable": str(executable),
            "command": command,
            "log_path": str(log_path),
        }
        response["native_joiner"] = status
        self._last_native_joiner_status = dict(status)
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
        connect_mode = str(getattr(room, "lan_connect_mode", "") or "relay").strip().lower()
        if connect_mode in {"lan", "no-relay", "norelay"}:
            connect_mode = "direct"
        elif connect_mode != "direct":
            connect_mode = "relay"
        port_layout = normalize_lan_port_layout(getattr(room, "lan_port_layout", ""))
        response["lan_connect_mode"] = connect_mode
        response["lan_port_layout"] = port_layout
        if connect_mode == "direct" or not bool(room.multiplayer_relay_enabled):
            self._multiplayer_relay.stop()
            self._loopback_relay.stop()
            self._udp_pair_relay.stop()
            skipped_reason = (
                "lan_connect_mode_direct"
                if connect_mode == "direct"
                else "multiplayer_relay_disabled"
            )
            response["multiplayer_relay"] = {
                "ok": True,
                "running": False,
                "skipped": skipped_reason,
                "lan_connect_mode": connect_mode,
                "lan_port_layout": port_layout,
            }
            response["loopback_relay"] = {
                "ok": True,
                "running": False,
                "skipped": skipped_reason,
                "lan_connect_mode": connect_mode,
                "lan_port_layout": port_layout,
            }
            response["udp_pair_relay"] = {
                "ok": True,
                "running": False,
                "skipped": skipped_reason,
                "lan_connect_mode": connect_mode,
                "lan_port_layout": port_layout,
            }
            return response

        peer_host = str(peer_host or select_room_connect_host(room) or "").strip()
        ports = derive_multiplayer_ports(room.start_port, room.multiplayer_relay_ports)
        #20260712_kpopmodder: LavLanSc2LadderServer now fixes the remote human as the first SC2 participant.
        # Its loopback dials the host bot second-player server ports, while its UDP pair owns first-player server ports.
        loopback_ports = derive_first_player_client_ports(room.start_port, port_layout)
        udp_local_ports = derive_first_player_server_ports(room.start_port, port_layout)
        udp_peer_ports = derive_second_player_server_ports(room.start_port, port_layout)
        bind_host = resolve_lan_bind_host(
            room.multiplayer_relay_bind_host,
            peer_host=peer_host,
        )
        relay_result = self._multiplayer_relay.start(
            bind_host=bind_host,
            ports=ports,
            target_host="127.0.0.1",
            enable_tcp=True,
            enable_udp=False,
        )
        relay_result["selected_peer_host"] = peer_host
        relay_result["selected_bind_host"] = bind_host
        relay_result["selected_ports"] = ports
        relay_result["lan_connect_mode"] = connect_mode
        relay_result["lan_port_layout"] = port_layout

        if peer_host:
            loopback_result = self._loopback_relay.start(
                bind_host="127.0.0.1",
                ports=loopback_ports,
                target_host=peer_host,
                enable_tcp=True,
                enable_udp=False,
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
                    "enable_udp": False,
                },
            }
        loopback_result["selected_peer_host"] = peer_host
        loopback_result["selected_bind_host"] = "127.0.0.1"
        loopback_result["selected_ports"] = loopback_ports
        loopback_result["lan_connect_mode"] = connect_mode
        loopback_result["lan_port_layout"] = port_layout

        if peer_host:
            udp_pair_result = self._udp_pair_relay.start(
                lan_bind_host=bind_host,
                peer_host=peer_host,
                local_ports=udp_local_ports,
                peer_ports=udp_peer_ports,
            )
        else:
            self._udp_pair_relay.stop()
            udp_pair_result = {
                "ok": False,
                "running": False,
                "error": "host_peer_missing",
                "config": {
                    "lan_bind_host": bind_host,
                    "local_bind_host": "127.0.0.1",
                    "peer_host": "",
                    "local_ports": udp_local_ports,
                    "peer_ports": udp_peer_ports,
                },
            }
        udp_pair_result["selected_peer_host"] = peer_host
        udp_pair_result["selected_bind_host"] = bind_host
        udp_pair_result["selected_local_ports"] = udp_local_ports
        udp_pair_result["selected_peer_ports"] = udp_peer_ports
        udp_pair_result["lan_connect_mode"] = connect_mode
        udp_pair_result["lan_port_layout"] = port_layout

        response["multiplayer_relay"] = relay_result
        response["loopback_relay"] = loopback_result
        response["udp_pair_relay"] = udp_pair_result
        if (
            not relay_result.get("ok", False)
            or not loopback_result.get("ok", False)
            or not udp_pair_result.get("ok", False)
        ):
            response["ok"] = False
            errors = [
                str(item.get("error") or "")
                for item in (relay_result, loopback_result, udp_pair_result)
                if isinstance(item, dict) and item.get("error")
            ]
            response["error"] = "; ".join(errors) or "multiplayer_relay_failed"
        return response


def _resolve_native_joiner_executable(native_joiner: dict[str, Any], base_dir: Path) -> tuple[Path | None, list[str]]:
    name = str(native_joiner.get("executable_name") or "LavLanRemoteJoiner.exe").strip() or "LavLanRemoteJoiner.exe"
    values = [
        native_joiner.get("executable_path"),
        native_joiner.get("executable"),
    ]
    candidates: list[Path] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            candidates.append(Path(text))
    candidates.extend(
        [
            base_dir / "bin" / name,
            base_dir / name,
            base_dir / "native" / "LavLanSc2LadderServer" / "bin" / name,
            base_dir.parent.parent / "LAV_v0.2" / "plugins" / "StarCraft2" / "native" / "LavLanSc2LadderServer" / "bin" / name,
            Path.cwd() / "bin" / name,
            Path.cwd() / name,
        ]
    )
    seen: set[str] = set()
    candidate_strings: list[str] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidate_strings.append(key)
        if candidate.is_file():
            return candidate, candidate_strings
    return None, candidate_strings


def _native_joiner_args(native_joiner: dict[str, Any], room: LanRoom) -> list[str]:
    start_port = _valid_port(native_joiner.get("start_port"), room.start_port)
    args = [
        "--player-name",
        _string_value(native_joiner.get("player_name"), "IdleProbe"),
        "--race",
        _string_value(native_joiner.get("race"), "Protoss"),
        "--sc2-host",
        _string_value(native_joiner.get("sc2_host"), "127.0.0.1"),
        "--sc2-port",
        str(_valid_port(native_joiner.get("sc2_port"), human_client_port(room))),
        "--start-port",
        str(start_port),
        "--status-port",
        str(_valid_port(native_joiner.get("status_port"), 5677)),
        "--opponent-id",
        _string_value(native_joiner.get("opponent_id"), "HUMAN"),
        "--ready-wait-sec",
        str(_positive_float(native_joiner.get("ready_wait_sec"), 10.0)),
    ]
    lan_game_host_ip = _string_value(native_joiner.get("lan_game_host_ip"), "")
    if lan_game_host_ip:
        args.extend(["--lan-game-host-ip", lan_game_host_ip])
    args.extend(
        [
            "--lan-connect-mode",
            _string_value(native_joiner.get("lan_connect_mode"), getattr(room, "lan_connect_mode", "relay") or "relay"),
            "--lan-port-layout",
            normalize_lan_port_layout(native_joiner.get("lan_port_layout") or getattr(room, "lan_port_layout", "")),
        ]
    )
    return args


def _native_joiner_log_path(base_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return base_dir / "logs" / f"lav_lan_remote_joiner_{stamp}.log"


def _native_joiner_creation_flags() -> int:
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return flags


def _string_value(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or str(default)


def _positive_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default
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

