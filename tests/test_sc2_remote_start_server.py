from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sc2_lan_discovery_client import (
    DEFAULT_REMOTE_START_PORT,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LAV_REMOTE_HUMAN_START_PROTOCOL,
    LAV_REMOTE_HUMAN_START_VERSION,
    LanRoom,
)
from sc2_remote_start_server import RemoteHumanStartServer


def sample_room() -> LanRoom:
    return LanRoom(
        protocol=LAV_LAN_ROOM_PROTOCOL,
        version=LAV_LAN_ROOM_VERSION,
        source_id="source-1",
        room_id="room-1",
        room_name="LAV StarCraft II",
        proxy_host="192.168.0.10",
        proxy_ports=[5677, 5678],
        start_port=5690,
        human_client_port=5679,
        remote_start_port=DEFAULT_REMOTE_START_PORT,
    )


class RemoteHumanStartServerTest(unittest.TestCase):
    def test_handle_request_launches_sc2_and_reports_ready(self) -> None:
        server = RemoteHumanStartServer(port=DEFAULT_REMOTE_START_PORT)
        server._room = sample_room()
        server._sc2_executable = Path(r"C:\SC2\SC2_x64.exe")
        process = Mock(pid=1234)
        process.poll.return_value = None
        payload = {
            "protocol": LAV_REMOTE_HUMAN_START_PROTOCOL,
            "version": LAV_REMOTE_HUMAN_START_VERSION,
            "room_id": "room-1",
        }

        with (
            patch.object(Path, "is_file", autospec=True, return_value=True),
            patch("sc2_remote_start_server.launch_sc2", return_value=process) as launch_sc2,
            patch("sc2_remote_start_server._wait_for_port", return_value=True) as wait_for_port,
            patch("sc2_remote_start_server.wait_for_sc2_api_ping") as wait_for_ping,
        ):
            wait_for_ping.return_value.ok = True
            wait_for_ping.return_value.attempts = 2
            wait_for_ping.return_value.error = ""
            response = server._handle_request(payload, ("192.168.0.10", 50000))

        self.assertTrue(response["ok"])
        self.assertEqual(1234, response["pid"])
        self.assertEqual(5679, response["human_client_port"])
        self.assertTrue(response["api_ready"])
        self.assertEqual(2, response["api_ready_attempts"])
        launch_sc2.assert_called_once()
        wait_for_port.assert_called_once()
        wait_for_ping.assert_called_once()

    def test_handle_request_waits_for_api_ping_before_ack(self) -> None:
        server = RemoteHumanStartServer(port=DEFAULT_REMOTE_START_PORT)
        server._room = sample_room()
        server._sc2_executable = Path(r"C:\SC2\SC2_x64.exe")
        process = Mock(pid=1234)
        process.poll.return_value = None
        payload = {
            "protocol": LAV_REMOTE_HUMAN_START_PROTOCOL,
            "version": LAV_REMOTE_HUMAN_START_VERSION,
            "room_id": "room-1",
        }

        with (
            patch.object(Path, "is_file", autospec=True, return_value=True),
            patch("sc2_remote_start_server.launch_sc2", return_value=process),
            patch("sc2_remote_start_server._wait_for_port", return_value=True),
            patch("sc2_remote_start_server.wait_for_sc2_api_ping") as wait_for_ping,
        ):
            wait_for_ping.return_value.ok = False
            wait_for_ping.return_value.attempts = 3
            wait_for_ping.return_value.error = "timeout"
            response = server._handle_request(payload, ("192.168.0.10", 50000))

        self.assertFalse(response["ok"])
        self.assertEqual("human_sc2_api_ping_not_ready", response["error"])
        self.assertTrue(response["port_ready"])
        self.assertFalse(response["api_ready"])
        self.assertEqual(3, response["api_ready_attempts"])
        self.assertEqual("timeout", response["api_ready_error"])

    def test_handle_request_rejects_room_mismatch(self) -> None:
        server = RemoteHumanStartServer(port=DEFAULT_REMOTE_START_PORT)
        server._room = sample_room()
        server._sc2_executable = Path(r"C:\SC2\SC2_x64.exe")

        response = server._handle_request(
            {
                "protocol": LAV_REMOTE_HUMAN_START_PROTOCOL,
                "version": LAV_REMOTE_HUMAN_START_VERSION,
                "room_id": "other-room",
            },
            ("192.168.0.10", 50000),
        )

        self.assertFalse(response["ok"])
        self.assertEqual("room_id_mismatch", response["error"])


if __name__ == "__main__":
    unittest.main()
