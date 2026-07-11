#20260711_kpopmodder: Added LAN-only relay for SC2 JoinGame ports that bind to loopback.
from __future__ import annotations

import socket
import threading
from collections.abc import Iterable
from typing import Any, Callable


LogCallback = Callable[[str], None]


DEFAULT_SC2_MULTIPLAYER_START_PORT = 5690
DEFAULT_SC2_MULTIPLAYER_RELAY_SPAN = 5
LOOPBACK_TARGET_HOST = "127.0.0.1"
WINDOWS_UDP_CONNRESET_ERRNO = 10054
SIO_UDP_CONNRESET = getattr(socket, "SIO_UDP_CONNRESET", 0x9800000C)


def derive_multiplayer_ports(
    start_port: Any = DEFAULT_SC2_MULTIPLAYER_START_PORT,
    explicit_ports: Any = None,
) -> list[int]:
    ports = _normalize_ports(explicit_ports)
    if ports:
        return ports
    base = _valid_port(start_port, DEFAULT_SC2_MULTIPLAYER_START_PORT)
    return [
        port
        for port in range(base + 1, base + DEFAULT_SC2_MULTIPLAYER_RELAY_SPAN + 1)
        if 0 < port < 65536
    ]


def derive_first_player_client_ports(
    start_port: Any = DEFAULT_SC2_MULTIPLAYER_START_PORT,
) -> list[int]:
    return derive_second_player_server_ports(start_port)


def derive_second_player_client_ports(
    start_port: Any = DEFAULT_SC2_MULTIPLAYER_START_PORT,
) -> list[int]:
    return derive_first_player_server_ports(start_port)


def derive_first_player_server_ports(
    start_port: Any = DEFAULT_SC2_MULTIPLAYER_START_PORT,
) -> list[int]:
    return _derive_join_port_pair(start_port, 2)


def derive_second_player_server_ports(
    start_port: Any = DEFAULT_SC2_MULTIPLAYER_START_PORT,
) -> list[int]:
    return _derive_join_port_pair(start_port, 4)


def resolve_lan_bind_host(
    preferred_host: Any = "",
    *,
    peer_host: Any = "",
    fallback_host: str = "",
) -> str:
    preferred = _clean_host(preferred_host)
    if preferred and not _is_loopback_or_unspecified(preferred):
        return preferred

    peer = _clean_host(peer_host)
    if peer and not _is_loopback_or_unspecified(peer):
        detected = _local_ip_for_peer(peer)
        if detected and not _is_loopback_or_unspecified(detected):
            return detected

    hostname_ip = _hostname_ip()
    if hostname_ip and not _is_loopback_or_unspecified(hostname_ip):
        return hostname_ip

    fallback = _clean_host(fallback_host)
    if fallback:
        return fallback
    return ""


class SC2LanPortRelayManager:
    def __init__(self, log_callback: LogCallback | None = None) -> None:
        self._log_callback = log_callback
        self._lock = threading.RLock()
        self._tcp_relays: list[_TcpPortRelay] = []
        self._udp_relays: list[_UdpPortRelay] = []
        self._config: dict[str, Any] = {}
        self._last_status: dict[str, Any] = {"running": False}

    def start(
        self,
        *,
        bind_host: str,
        ports: Iterable[int],
        target_host: str = LOOPBACK_TARGET_HOST,
        enable_tcp: bool = True,
        enable_udp: bool = True,
    ) -> dict[str, Any]:
        ports = _normalize_ports(list(ports))
        bind_host = _clean_host(bind_host)
        target_host = _clean_host(target_host) or LOOPBACK_TARGET_HOST
        config = {
            "bind_host": bind_host,
            "target_host": target_host,
            "ports": ports,
            "enable_tcp": bool(enable_tcp),
            "enable_udp": bool(enable_udp),
        }
        if not bind_host:
            status = {"ok": False, "running": False, "error": "lan_bind_host_missing", "config": config}
            with self._lock:
                self._last_status = status
            return status
        if not ports:
            status = {"ok": False, "running": False, "error": "relay_ports_missing", "config": config}
            with self._lock:
                self._last_status = status
            return status

        with self._lock:
            if (
                self.is_running()
                and self._config == config
                and self._last_status.get("ok", False)
            ):
                self._last_status = self.status()
                self._last_status["ok"] = True
                self._last_status["message"] = "already_running"
                return dict(self._last_status)
            self.stop()
            errors: list[str] = []
            if enable_tcp:
                for port in ports:
                    relay = _TcpPortRelay(
                        bind_host=bind_host,
                        port=port,
                        target_host=target_host,
                        target_port=port,
                        log_callback=self._log,
                    )
                    result = relay.start()
                    self._tcp_relays.append(relay)
                    if not result.get("ok"):
                        errors.append(f"tcp:{port}:{result.get('error')}")
            if enable_udp:
                for port in ports:
                    relay = _UdpPortRelay(
                        bind_host=bind_host,
                        port=port,
                        target_host=target_host,
                        target_port=port,
                        log_callback=self._log,
                    )
                    result = relay.start()
                    self._udp_relays.append(relay)
                    if not result.get("ok"):
                        errors.append(f"udp:{port}:{result.get('error')}")
            self._config = config
            self._last_status = self.status()
            self._last_status["ok"] = not errors
            if errors:
                self._last_status["error"] = "; ".join(errors)
            else:
                self._last_status["message"] = "started"
            return dict(self._last_status)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            for relay in self._tcp_relays:
                relay.stop()
            for relay in self._udp_relays:
                relay.stop()
            self._tcp_relays = []
            self._udp_relays = []
            self._config = {}
            self._last_status = {"running": False}
            return dict(self._last_status)

    def is_running(self) -> bool:
        return any(relay.is_running() for relay in self._tcp_relays + self._udp_relays)

    def status(self) -> dict[str, Any]:
        with self._lock:
            tcp = [relay.status() for relay in self._tcp_relays]
            udp = [relay.status() for relay in self._udp_relays]
            return {
                "running": any(item.get("running") for item in tcp + udp),
                "config": dict(self._config),
                "tcp": tcp,
                "udp": udp,
            }

    def _log(self, message: str) -> None:
        if callable(self._log_callback):
            try:
                self._log_callback(message)
            except Exception:
                pass


class SC2UdpPortPairRelayManager:
    def __init__(self, log_callback: LogCallback | None = None) -> None:
        self._log_callback = log_callback
        self._lock = threading.RLock()
        self._relays: list[_UdpPortPairRelay] = []
        self._config: dict[str, Any] = {}
        self._last_status: dict[str, Any] = {"running": False}

    def start(
        self,
        *,
        lan_bind_host: str,
        peer_host: str,
        local_ports: Iterable[int],
        peer_ports: Iterable[int],
        local_bind_host: str = LOOPBACK_TARGET_HOST,
    ) -> dict[str, Any]:
        local_ports = _normalize_ports(list(local_ports))
        peer_ports = _normalize_ports(list(peer_ports))
        lan_bind_host = _clean_host(lan_bind_host)
        peer_host = _clean_host(peer_host)
        local_bind_host = _clean_host(local_bind_host) or LOOPBACK_TARGET_HOST
        config = {
            "lan_bind_host": lan_bind_host,
            "local_bind_host": local_bind_host,
            "peer_host": peer_host,
            "local_ports": local_ports,
            "peer_ports": peer_ports,
        }
        if not lan_bind_host:
            status = {"ok": False, "running": False, "error": "lan_bind_host_missing", "config": config}
            with self._lock:
                self._last_status = status
            return status
        if not peer_host:
            status = {"ok": False, "running": False, "error": "peer_host_missing", "config": config}
            with self._lock:
                self._last_status = status
            return status
        if not local_ports or not peer_ports or len(local_ports) != len(peer_ports):
            status = {"ok": False, "running": False, "error": "port_pair_mismatch", "config": config}
            with self._lock:
                self._last_status = status
            return status

        with self._lock:
            if (
                self.is_running()
                and self._config == config
                and self._last_status.get("ok", False)
            ):
                self._last_status = self.status()
                self._last_status["ok"] = True
                self._last_status["message"] = "already_running"
                return dict(self._last_status)
            self.stop()
            errors: list[str] = []
            for local_port, peer_port in zip(local_ports, peer_ports):
                relay = _UdpPortPairRelay(
                    lan_bind_host=lan_bind_host,
                    local_bind_host=local_bind_host,
                    peer_host=peer_host,
                    local_port=local_port,
                    peer_port=peer_port,
                    log_callback=self._log,
                )
                result = relay.start()
                self._relays.append(relay)
                if not result.get("ok"):
                    errors.append(f"udp:{local_port}<->{peer_port}:{result.get('error')}")
            self._config = config
            self._last_status = self.status()
            self._last_status["ok"] = not errors
            if errors:
                self._last_status["error"] = "; ".join(errors)
            else:
                self._last_status["message"] = "started"
            return dict(self._last_status)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            for relay in self._relays:
                relay.stop()
            self._relays = []
            self._config = {}
            self._last_status = {"running": False}
            return dict(self._last_status)

    def is_running(self) -> bool:
        return any(relay.is_running() for relay in self._relays)

    def status(self) -> dict[str, Any]:
        with self._lock:
            relays = [relay.status() for relay in self._relays]
            return {
                "running": any(item.get("running") for item in relays),
                "config": dict(self._config),
                "relays": relays,
            }

    def _log(self, message: str) -> None:
        if callable(self._log_callback):
            try:
                self._log_callback(message)
            except Exception:
                pass


class _TcpPortRelay:
    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        target_host: str,
        target_port: int,
        log_callback: LogCallback,
    ) -> None:
        self.bind_host = bind_host
        self.port = int(port)
        self.target_host = target_host
        self.target_port = int(target_port)
        self._log = log_callback
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._last_error = ""

    def start(self) -> dict[str, Any]:
        self._stop.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_host, self.port))
            sock.listen(16)
            sock.settimeout(0.5)
        except OSError as exc:
            self._last_error = str(exc)
            try:
                sock.close()  # type: ignore[name-defined]
            except Exception:
                pass
            self._log(f"TCP relay bind failed {self.bind_host}:{self.port} -> {self.target_host}:{self.target_port}: {exc}")
            return {"ok": False, "error": str(exc), **self.status()}
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, name=f"SC2LanTcpRelay.{self.port}", daemon=True)
        self._thread.start()
        self._log(f"TCP relay listening {self.bind_host}:{self.port} -> {self.target_host}:{self.target_port}")
        return {"ok": True, **self.status()}

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
        self._sock = None
        self._thread = None

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {
            "protocol": "tcp",
            "running": self.is_running(),
            "bind_host": self.bind_host,
            "port": self.port,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "last_error": self._last_error,
        }

    def _accept_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                client, address = sock.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._last_error = str(exc)
                    self._log(f"TCP relay accept failed {self.bind_host}:{self.port}: {exc}")
                break
            threading.Thread(
                target=self._handle_client,
                args=(client, address),
                name=f"SC2LanTcpRelay.{self.port}.client",
                daemon=True,
            ).start()

    def _handle_client(self, client: socket.socket, address: tuple[str, int]) -> None:
        try:
            target = socket.create_connection((self.target_host, self.target_port), timeout=5.0)
        except OSError as exc:
            self._last_error = str(exc)
            self._log(f"TCP relay target connect failed {address} -> {self.target_host}:{self.target_port}: {exc}")
            try:
                client.close()
            except OSError:
                pass
            return

        self._log(f"TCP relay connected {address} -> {self.target_host}:{self.target_port}")
        stop = threading.Event()
        threads = [
            threading.Thread(target=_pipe_tcp, args=(client, target, stop), name=f"SC2LanTcpRelay.{self.port}.c2t", daemon=True),
            threading.Thread(target=_pipe_tcp, args=(target, client, stop), name=f"SC2LanTcpRelay.{self.port}.t2c", daemon=True),
        ]
        for thread in threads:
            thread.start()
        while not stop.is_set() and any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
        stop.set()
        _safe_close_socket(client)
        _safe_close_socket(target)


class _UdpPortPairRelay:
    def __init__(
        self,
        *,
        lan_bind_host: str,
        local_bind_host: str,
        peer_host: str,
        local_port: int,
        peer_port: int,
        log_callback: LogCallback,
    ) -> None:
        self.lan_bind_host = lan_bind_host
        self.local_bind_host = local_bind_host
        self.peer_host = peer_host
        self.local_port = int(local_port)
        self.peer_port = int(peer_port)
        self._log = log_callback
        self._stop = threading.Event()
        self._lan_sock: socket.socket | None = None
        self._local_sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._last_error = ""
        self._lan_to_local_packets = 0
        self._local_to_lan_packets = 0
        self._lan_recv_reset_count = 0
        self._local_recv_reset_count = 0

    def start(self) -> dict[str, Any]:
        self._stop.clear()
        try:
            lan_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_socket(lan_sock)
            lan_sock.bind((self.lan_bind_host, self.local_port))
            lan_sock.settimeout(0.5)
            local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_socket(local_sock)
            local_sock.bind((self.local_bind_host, self.peer_port))
            local_sock.settimeout(0.5)
        except OSError as exc:
            self._last_error = str(exc)
            try:
                lan_sock.close()  # type: ignore[name-defined]
            except Exception:
                pass
            try:
                local_sock.close()  # type: ignore[name-defined]
            except Exception:
                pass
            self._log(
                "UDP preserved relay bind failed "
                f"LAN {self.lan_bind_host}:{self.local_port} "
                f"<-> local {self.local_bind_host}:{self.peer_port} "
                f"peer={self.peer_host}:{self.peer_port}: {exc}"
            )
            return {"ok": False, "error": str(exc), **self.status()}

        self._lan_sock = lan_sock
        self._local_sock = local_sock
        self._threads = [
            threading.Thread(
                target=self._lan_to_local_loop,
                name=f"SC2UdpPairRelay.{self.local_port}.lan_to_local",
                daemon=True,
            ),
            threading.Thread(
                target=self._local_to_lan_loop,
                name=f"SC2UdpPairRelay.{self.local_port}.local_to_lan",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()
        self._log(
            "UDP preserved relay listening "
            f"LAN {self.lan_bind_host}:{self.local_port} "
            f"<-> local {self.local_bind_host}:{self.peer_port} "
            f"peer={self.peer_host}:{self.peer_port}"
        )
        return {"ok": True, **self.status()}

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._lan_sock, self._local_sock):
            if sock is not None:
                _safe_close_socket(sock)
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        self._lan_sock = None
        self._local_sock = None
        self._threads = []

    def is_running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def status(self) -> dict[str, Any]:
        return {
            "protocol": "udp_port_pair",
            "running": self.is_running(),
            "lan_bind_host": self.lan_bind_host,
            "local_bind_host": self.local_bind_host,
            "peer_host": self.peer_host,
            "local_port": self.local_port,
            "peer_port": self.peer_port,
            "lan_to_local_packets": self._lan_to_local_packets,
            "local_to_lan_packets": self._local_to_lan_packets,
            "lan_recv_reset_count": self._lan_recv_reset_count,
            "local_recv_reset_count": self._local_recv_reset_count,
            "last_error": self._last_error,
        }

    def _lan_to_local_loop(self) -> None:
        lan_sock = self._lan_sock
        local_sock = self._local_sock
        if lan_sock is None or local_sock is None:
            return
        while not self._stop.is_set():
            try:
                payload, peer = lan_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if _is_udp_connreset(exc):
                    self._lan_recv_reset_count += 1
                    self._last_error = str(exc)
                    if self._should_log_reset(self._lan_recv_reset_count):
                        self._log(
                            f"UDP preserved LAN recv reset ignored {self.lan_bind_host}:{self.local_port}: {exc}"
                        )
                    continue
                if not self._stop.is_set():
                    self._last_error = str(exc)
                    self._log(f"UDP preserved LAN recv failed {self.lan_bind_host}:{self.local_port}: {exc}")
                break
            if not payload:
                continue
            self._lan_to_local_packets += 1
            if self._lan_to_local_packets == 1 or int(peer[1]) != self.peer_port:
                self._log(
                    "UDP preserved LAN->local "
                    f"{peer[0]}:{peer[1]} -> {self.local_bind_host}:{self.local_port} "
                    f"source={self.local_bind_host}:{self.peer_port}"
                )
            try:
                local_sock.sendto(payload, (self.local_bind_host, self.local_port))
            except OSError as exc:
                self._last_error = str(exc)
                self._log(
                    "UDP preserved LAN->local send failed "
                    f"{peer[0]}:{peer[1]} -> {self.local_bind_host}:{self.local_port}: {exc}"
                )

    def _local_to_lan_loop(self) -> None:
        lan_sock = self._lan_sock
        local_sock = self._local_sock
        if lan_sock is None or local_sock is None:
            return
        while not self._stop.is_set():
            try:
                payload, peer = local_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if _is_udp_connreset(exc):
                    self._local_recv_reset_count += 1
                    self._last_error = str(exc)
                    if self._should_log_reset(self._local_recv_reset_count):
                        self._log(
                            f"UDP preserved local recv reset ignored {self.local_bind_host}:{self.peer_port}: {exc}"
                        )
                    continue
                if not self._stop.is_set():
                    self._last_error = str(exc)
                    self._log(f"UDP preserved local recv failed {self.local_bind_host}:{self.peer_port}: {exc}")
                break
            if not payload:
                continue
            self._local_to_lan_packets += 1
            if self._local_to_lan_packets == 1 or int(peer[1]) != self.local_port:
                self._log(
                    "UDP preserved local->LAN "
                    f"{peer[0]}:{peer[1]} -> {self.peer_host}:{self.peer_port} "
                    f"source={self.lan_bind_host}:{self.local_port}"
                )
            try:
                lan_sock.sendto(payload, (self.peer_host, self.peer_port))
            except OSError as exc:
                self._last_error = str(exc)
                self._log(
                    "UDP preserved local->LAN send failed "
                    f"{peer[0]}:{peer[1]} -> {self.peer_host}:{self.peer_port}: {exc}"
                )

    def _should_log_reset(self, count: int) -> bool:
        return count <= 3 or count % 10 == 0


class _UdpPortRelay:
    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        target_host: str,
        target_port: int,
        log_callback: LogCallback,
    ) -> None:
        self.bind_host = bind_host
        self.port = int(port)
        self.target_host = target_host
        self.target_port = int(target_port)
        self._log = log_callback
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._peers: dict[tuple[str, int], socket.socket] = {}
        self._last_error = ""

    def start(self) -> dict[str, Any]:
        self._stop.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_socket(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_host, self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            self._last_error = str(exc)
            try:
                sock.close()  # type: ignore[name-defined]
            except Exception:
                pass
            self._log(f"UDP relay bind failed {self.bind_host}:{self.port} -> {self.target_host}:{self.target_port}: {exc}")
            return {"ok": False, "error": str(exc), **self.status()}
        self._sock = sock
        self._thread = threading.Thread(target=self._recv_loop, name=f"SC2LanUdpRelay.{self.port}", daemon=True)
        self._thread.start()
        self._log(f"UDP relay listening {self.bind_host}:{self.port} -> {self.target_host}:{self.target_port}")
        return {"ok": True, **self.status()}

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        with self._lock:
            peer_sockets = list(self._peers.values())
            self._peers = {}
        for peer_sock in peer_sockets:
            _safe_close_socket(peer_sock)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._sock = None
        self._thread = None

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict[str, Any]:
        with self._lock:
            peer_count = len(self._peers)
        return {
            "protocol": "udp",
            "running": self.is_running(),
            "bind_host": self.bind_host,
            "port": self.port,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "peer_count": peer_count,
            "last_error": self._last_error,
        }

    def _recv_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                payload, peer = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._last_error = str(exc)
                    self._log(f"UDP relay recv failed {self.bind_host}:{self.port}: {exc}")
                break
            if not payload:
                continue
            local_sock = self._peer_socket(peer)
            try:
                local_sock.sendto(payload, (self.target_host, self.target_port))
            except OSError as exc:
                self._last_error = str(exc)
                self._log(f"UDP relay send-to-target failed {peer} -> {self.target_host}:{self.target_port}: {exc}")

    def _peer_socket(self, peer: tuple[str, int]) -> socket.socket:
        with self._lock:
            existing = self._peers.get(peer)
            if existing is not None:
                return existing
            local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_socket(local_sock)
            #20260711_kpopmodder: Loopback->LAN relay must let Windows choose
            # the outbound LAN interface, while LAN->loopback relay must stay
            # pinned to 127.0.0.1 so SC2 sees local traffic.
            reply_bind_host = (
                LOOPBACK_TARGET_HOST
                if _is_loopback_target(self.target_host)
                else "0.0.0.0"
            )
            local_sock.bind((reply_bind_host, 0))
            local_sock.settimeout(0.5)
            self._peers[peer] = local_sock
            threading.Thread(
                target=self._peer_reply_loop,
                args=(peer, local_sock),
                name=f"SC2LanUdpRelay.{self.port}.peer",
                daemon=True,
            ).start()
            self._log(f"UDP relay peer mapped {peer} -> {self.target_host}:{self.target_port}")
            return local_sock

    def _peer_reply_loop(self, peer: tuple[str, int], local_sock: socket.socket) -> None:
        external = self._sock
        while not self._stop.is_set() and external is not None:
            try:
                payload, _ = local_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                external.sendto(payload, peer)
            except OSError as exc:
                self._last_error = str(exc)
                self._log(f"UDP relay send-to-peer failed {self.target_host}:{self.target_port} -> {peer}: {exc}")
                break
        with self._lock:
            current = self._peers.get(peer)
            if current is local_sock:
                self._peers.pop(peer, None)
        _safe_close_socket(local_sock)


def _pipe_tcp(source: socket.socket, target: socket.socket, stop: threading.Event) -> None:
    try:
        source.settimeout(0.5)
    except OSError:
        return
    while not stop.is_set():
        try:
            chunk = source.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        try:
            target.sendall(chunk)
        except OSError:
            break
    stop.set()
    try:
        target.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _configure_udp_socket(sock: socket.socket) -> None:
    #20260711_kpopmodder: Windows may surface ICMP port-unreachable as
    # WSAECONNRESET on UDP recv. During SC2 JoinGame both clients can briefly
    # probe ports before the peer is fully ready, so keep the relay alive.
    ioctl = getattr(sock, "ioctl", None)
    if not callable(ioctl):
        return
    try:
        ioctl(SIO_UDP_CONNRESET, False)
    except (OSError, ValueError, AttributeError):
        pass


def _is_udp_connreset(exc: OSError) -> bool:
    values = [
        getattr(exc, "winerror", None),
        getattr(exc, "errno", None),
    ]
    return WINDOWS_UDP_CONNRESET_ERRNO in values


def _normalize_ports(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, Iterable):
        raw_items = list(value)
    else:
        raw_items = [value]
    ports: list[int] = []
    for item in raw_items:
        port = _valid_port(item, 0)
        if port and port not in ports:
            ports.append(port)
    return ports


def _derive_join_port_pair(start_port: Any, offset: int) -> list[int]:
    base = _valid_port(start_port, DEFAULT_SC2_MULTIPLAYER_START_PORT)
    return [
        port
        for port in (base + int(offset), base + int(offset) + 1)
        if 0 < port < 65536
    ]


def _valid_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return int(default)
    if 0 < port < 65536:
        return port
    return int(default)


def _clean_host(value: Any) -> str:
    return str(value or "").strip().strip("[]")


def _is_loopback_or_unspecified(value: str) -> bool:
    text = _clean_host(value).lower()
    return (
        not text
        or text in {"0.0.0.0", "::", "localhost", "::1"}
        or text.startswith("127.")
    )


def _is_loopback_target(value: str) -> bool:
    text = _clean_host(value).lower()
    return text in {"localhost", "::1"} or text.startswith("127.")


def _local_ip_for_peer(peer_host: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((peer_host, 9))
            return str(sock.getsockname()[0] or "")
    except OSError:
        return ""


def _hostname_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return ""


def _safe_close_socket(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass
