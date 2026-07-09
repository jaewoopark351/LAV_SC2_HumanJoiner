from __future__ import annotations

import json
import unittest

from sc2_lan_discovery_client import (
    DEFAULT_JOIN_PORT,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LanRoomPayloadError,
    LanRoomRegistry,
    LanScanDiagnostics,
    parse_lav_lan_room_payload,
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
        self.assertEqual(room.room_state, "waiting")
        self.assertEqual(room.last_seen, 100.0)
        self.assertEqual(room.sender_ip, "192.168.0.67")

    def test_rejects_wrong_protocol(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(protocol="other"), received_at=100.0)

    def test_rejects_missing_room_id(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(room_id=""), received_at=100.0)

    def test_rejects_non_integer_proxy_port(self) -> None:
        with self.assertRaises(LanRoomPayloadError):
            parse_lav_lan_room_payload(sample_payload(proxy_ports=[5677, "5678"]), received_at=100.0)


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


if __name__ == "__main__":
    unittest.main()
