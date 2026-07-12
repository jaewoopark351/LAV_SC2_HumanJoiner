from __future__ import annotations

import json
import socket
import threading
import unittest

from sc2_lan_discovery_client import (
    DEFAULT_JOIN_PORT,
    DEFAULT_MAP_DOWNLOAD_PORT,
    DEFAULT_REMOTE_START_PORT,
    LAV_LOBBY_JOIN_ACK_PROTOCOL,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LanRoomPayloadError,
    LanRoomRegistry,
    LanScanDiagnostics,
    parse_lav_lan_room_payload,
    send_lobby_join,
    select_room_connect_host,
)


def sample_payload(**overrides: object) -> str:
    payload = {
        "protocol": LAV_LAN_ROOM_PROTOCOL,
        "version": LAV_LAN_ROOM_VERSION,
        "source_id": "source-1",
        "room_id": "room-1",
        "room_name": "LAV StarCraft II",
        "host_name": "AI-PC",
        "player_name": "LAV",
        "mode": "observer",
        "preferred_bot": "Changeling",
        "preferred_map": "IncorporealAIE_v4",
        "proxy_host": "192.168.0.67",
        "proxy_ports": [5677, 5678],
        "start_port": 5690,
        "join_port": DEFAULT_JOIN_PORT,
        "human_client_port": 5679,
        "remote_start_port": DEFAULT_REMOTE_START_PORT,
        "map_file_name": "IncorporealAIE_v4.SC2Map",
        "map_size": 4589,
        "map_sha256": "abc123",
        "map_download_port": DEFAULT_MAP_DOWNLOAD_PORT,
        "map_download_path": "/map/IncorporealAIE_v4.SC2Map",
        "room_state": "waiting",
        "timestamp": 0,
        "expires_sec": 10,
    }
    payload.update(overrides)
    return json.dumps(payload)


class ParseLavLanRoomPayloadTest(unittest.TestCase):
    def test_parses_expected_payload(self) -> None:
        room = parse_lav_lan_room_payload(sample_payload(), received_at=100.0, sender_ip="192.168.0.67")

        self.assertEqual(room.room_name, "LAV StarCraft II")
        self.assertEqual(room.host_name, "AI-PC")
        self.assertEqual(room.preferred_bot, "Changeling")
        self.assertEqual(room.preferred_map, "IncorporealAIE_v4")
        self.assertEqual(room.proxy_host, "192.168.0.67")
        self.assertEqual(room.proxy_ports, [5677, 5678])
        self.assertEqual(room.start_port, 5690)
        self.assertEqual(room.join_port, DEFAULT_JOIN_PORT)
        self.assertEqual(room.human_client_port, 5679)
        self.assertEqual(room.remote_start_port, DEFAULT_REMOTE_START_PORT)
        self.assertEqual(room.map_file_name, "IncorporealAIE_v4.SC2Map")
        self.assertEqual(room.map_size, 4589)
        self.assertEqual(room.map_sha256, "abc123")
        self.assertEqual(room.map_download_port, DEFAULT_MAP_DOWNLOAD_PORT)
        self.assertEqual(room.map_download_path, "/map/IncorporealAIE_v4.SC2Map")
        self.assertEqual(room.room_state, "waiting")
        self.assertEqual(room.last_seen, 100.0)
        self.assertEqual(room.sender_ip, "192.168.0.67")
        self.assertEqual(room.lan_port_layout, "s2client-api-shared")

    def test_parses_advertised_s2client_api_layout(self) -> None:
        room = parse_lav_lan_room_payload(
            sample_payload(lan_port_layout="s2client-api-shared"),
            received_at=100.0,
            sender_ip="192.168.0.67",
        )

        self.assertEqual(room.lan_port_layout, "s2client-api-shared")

    def test_rejects_wrong_protocol(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(protocol="other"), received_at=100.0)

    def test_rejects_missing_room_id(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(room_id=""), received_at=100.0)

    def test_rejects_non_integer_proxy_port(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(proxy_ports=[5677, "5678"]), received_at=100.0)

    def test_connect_host_prefers_observed_sender_ip_over_stale_proxy_host(self) -> None:
        room = parse_lav_lan_room_payload(
            sample_payload(proxy_host="26.189.202.71"),
            received_at=100.0,
            sender_ip="192.168.0.26",
        )

        self.assertEqual("192.168.0.26", select_room_connect_host(room))


class LanRoomRegistryTest(unittest.TestCase):
    def test_updates_duplicate_by_room_and_source(self) -> None:
        registry = LanRoomRegistry()
        original = parse_lav_lan_room_payload(sample_payload(preferred_bot="Changeling"), received_at=100.0)
        updated = parse_lav_lan_room_payload(sample_payload(preferred_bot="Marine"), received_at=105.0)

        registry.update(original)
        registry.update(updated)

        rooms = registry.rooms(now=106.0)
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0].preferred_bot, "Marine")
        self.assertEqual(rooms[0].last_seen, 105.0)

    def test_removes_expired_rooms(self) -> None:
        registry = LanRoomRegistry()
        room = parse_lav_lan_room_payload(sample_payload(expires_sec=5), received_at=100.0)

        registry.update(room)

        self.assertEqual(len(registry.rooms(now=104.9)), 1)
        self.assertEqual(registry.rooms(now=105.0), [])


class LanScanDiagnosticsTest(unittest.TestCase):
    def test_records_bind_packets_parse_failures_and_senders(self) -> None:
        diagnostics = LanScanDiagnostics()

        diagnostics.start(bind_host="", port=47624, duration_sec=15.0)
        diagnostics.record_bind_success()
        failed_event = diagnostics.record_packet("26.189.202.71", 50000, b"bad payload")
        diagnostics.record_parse_failure("unsupported version", failed_event)
        ok_event = diagnostics.record_packet("26.189.202.71", 50001, sample_payload().encode("utf-8"))
        diagnostics.record_parse_success(ok_event)
        diagnostics.finish(rooms_returned=1)

        self.assertEqual(diagnostics.bind_host, "")
        self.assertEqual(diagnostics.port, 47624)
        self.assertTrue(diagnostics.bind_succeeded)
        self.assertEqual(diagnostics.packets_received, 2)
        self.assertEqual(diagnostics.parse_failures, 1)
        self.assertEqual(diagnostics.parse_failure_reasons, {"unsupported version": 1})
        self.assertEqual(diagnostics.sender_counts, {"26.189.202.71": 2})
        self.assertEqual(diagnostics.parsed_rooms, 1)
        self.assertEqual(diagnostics.rooms_returned, 1)
        self.assertEqual(diagnostics.packet_events[0].sender_port, 50000)
        self.assertIn("bad payload", diagnostics.packet_events[0].raw_payload)
        self.assertEqual(diagnostics.packet_events[0].parse_result, "FAILED reason=unsupported version")
        self.assertEqual(diagnostics.packet_events[1].sender_port, 50001)
        self.assertEqual(diagnostics.packet_events[1].parse_result, "OK")
        self.assertTrue(diagnostics.started_at)
        self.assertTrue(diagnostics.ended_at)


class LobbyJoinTest(unittest.TestCase):
    def test_send_lobby_join_prefers_tcp_ack(self) -> None:
        room = parse_lav_lan_room_payload(sample_payload(), received_at=100.0, sender_ip="127.0.0.1")
        port = free_tcp_port()
        received_payloads: list[dict[str, object]] = []
        thread = threading.Thread(
            target=tcp_join_ack_server,
            args=(port, received_payloads),
            daemon=True,
        )
        thread.start()

        result = send_lobby_join(
            room,
            player_name="Tester",
            timeout_sec=2.0,
            client_id="client-1",
            target_host="127.0.0.1",
            target_port=port,
        )
        thread.join(timeout=2.0)

        self.assertTrue(result.ok, result.error)
        self.assertEqual("client-1", result.client_id)
        self.assertEqual("Tester", received_payloads[0]["player_name"])
        self.assertEqual("s2client-api-shared", received_payloads[0]["lan_port_layout"])

    def test_send_lobby_join_falls_back_to_udp_ack(self) -> None:
        room = parse_lav_lan_room_payload(sample_payload(), received_at=100.0, sender_ip="127.0.0.1")
        port = free_udp_port()
        received_payloads: list[dict[str, object]] = []
        thread = threading.Thread(
            target=udp_join_ack_server,
            args=(port, received_payloads),
            daemon=True,
        )
        thread.start()

        result = send_lobby_join(
            room,
            player_name="Tester",
            timeout_sec=2.0,
            client_id="client-udp",
            target_host="127.0.0.1",
            target_port=port,
        )
        thread.join(timeout=2.0)

        self.assertTrue(result.ok, result.error)
        self.assertEqual("client-udp", result.client_id)
        self.assertEqual("Tester", received_payloads[0]["player_name"])
        self.assertEqual("s2client-api-shared", received_payloads[0]["lan_port_layout"])


def tcp_join_ack_server(port: int, received_payloads: list[dict[str, object]]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)
        conn, _ = server.accept()
        with conn:
            chunks = []
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                if len(chunk) < 8192:
                    break
            payload = json.loads(b"".join(chunks).decode("utf-8"))
            received_payloads.append(payload)
            conn.sendall(join_ack(payload))


def udp_join_ack_server(port: int, received_payloads: list[dict[str, object]]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", port))
        data, address = server.recvfrom(8192)
        payload = json.loads(data.decode("utf-8"))
        received_payloads.append(payload)
        server.sendto(join_ack(payload), address)


def join_ack(payload: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "protocol": LAV_LOBBY_JOIN_ACK_PROTOCOL,
            "version": 1,
            "ok": True,
            "message": "joined",
            "client_id": payload.get("client_id", ""),
        }
    ).encode("utf-8")


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
