from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import human_joiner
from sc2_join_launcher import PortCheck
from sc2_lan_discovery_client import DEFAULT_JOIN_PORT, LobbyJoinResult


class HumanJoinerCliTest(unittest.TestCase):
    def test_parse_proxy_ports_deduplicates_valid_ports(self) -> None:
        self.assertEqual(human_joiner.parse_proxy_ports("5677,5678,5677"), [5677, 5678])

    def test_scan_debug_no_rooms_prints_manual_fallback(self) -> None:
        class FakeNoRoomsClient:
            saw_log_payloads = False

            def __init__(self, *, port: int) -> None:
                self.port = port

            def scan(self, *, duration_sec, diagnostics=None, debug_logger=None, log_payloads=False, **_) -> list:
                FakeNoRoomsClient.saw_log_payloads = bool(log_payloads)
                if diagnostics is not None:
                    diagnostics.start(bind_host="", port=self.port, duration_sec=duration_sec)
                    diagnostics.record_bind_success()
                    diagnostics.finish(rooms_returned=0)
                return []

        with (
            patch("human_joiner.LanDiscoveryClient", FakeNoRoomsClient),
            patch("human_joiner.find_sc2_executable", return_value=None),
        ):
            code, output = self._run_cli(["scan", "--seconds", "1", "--debug"])

        self.assertEqual(code, 1)
        self.assertTrue(FakeNoRoomsClient.saw_log_payloads)
        self.assertIn("UDP scan debug", output)
        self.assertIn("Bind host: 0.0.0.0", output)
        self.assertIn("UDP packets received: 0", output)
        self.assertIn("Parse success count: 0", output)
        self.assertIn("Parse failed count: 0", output)
        self.assertIn("Final rooms count: 0", output)
        self.assertIn("UDP packet events: none", output)
        self.assertIn("No LAV StarCraft II rooms found on UDP port 47624.", output)
        self.assertIn("Manual host fallback is available", output)
        self.assertIn("Default manual host: 26.189.202.71", output)
        self.assertIn("Default proxy ports: 5677,5678", output)

    def test_check_manual_host_reports_reachable_ports(self) -> None:
        checks = [
            PortCheck("192.168.0.67", 5677, True),
            PortCheck("192.168.0.67", 5678, True),
        ]

        with patch("human_joiner.find_sc2_executable", return_value=None):
            with patch("human_joiner.check_proxy_ports", return_value=checks):
                code, output = self._run_cli(
                    ["check", "--host", "192.168.0.67", "--proxy-ports", "5677,5678"]
                )

        self.assertEqual(code, 0)
        self.assertIn("OK 192.168.0.67:5677", output)
        self.assertIn("OK 192.168.0.67:5678", output)

    def test_join_manual_host_launches_after_proxy_check(self) -> None:
        checks = [
            PortCheck("192.168.0.67", 5677, True),
            PortCheck("192.168.0.67", 5678, True),
        ]
        process = Mock(pid=1234)

        with patch("human_joiner.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")):
            with patch("human_joiner.check_proxy_ports", return_value=checks):
                with patch("human_joiner.launch_sc2", return_value=process) as launch_sc2:
                    code, output = self._run_cli(
                        ["join", "--host", "192.168.0.67", "--proxy-ports", "5677,5678"]
                    )

        self.assertEqual(code, 0)
        self.assertIn("StarCraft II launch requested. PID: 1234", output)
        launch_sc2.assert_called_once()

    def test_join_manual_host_launches_even_when_proxy_is_unreachable_by_default(self) -> None:
        checks = [PortCheck("192.168.0.67", 5677, False, "timed out")]
        process = Mock(pid=1234)

        with patch("human_joiner.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")):
            with patch("human_joiner.check_proxy_ports", return_value=checks):
                with patch("human_joiner.launch_sc2", return_value=process) as launch_sc2:
                    code, output = self._run_cli(
                        ["join", "--host", "192.168.0.67", "--proxy-ports", "5677"]
                    )

        self.assertEqual(code, 0)
        self.assertIn("StarCraft II launch requested. PID: 1234", output)
        launch_sc2.assert_called_once()

    def test_join_manual_host_can_require_proxy_check(self) -> None:
        checks = [PortCheck("192.168.0.67", 5677, False, "timed out")]

        with patch("human_joiner.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")):
            with patch("human_joiner.check_proxy_ports", return_value=checks):
                with patch("human_joiner.launch_sc2") as launch_sc2:
                    code, output = self._run_cli(
                        [
                            "join",
                            "--host",
                            "192.168.0.67",
                            "--proxy-ports",
                            "5677",
                            "--require-proxy-check",
                        ]
                    )

        self.assertEqual(code, 2)
        self.assertIn("Join aborted", output)
        launch_sc2.assert_not_called()

    def test_lobby_manual_host_sends_lobby_join(self) -> None:
        result = LobbyJoinResult(
            ok=True,
            target_host="192.168.0.67",
            target_port=DEFAULT_JOIN_PORT,
            room_id="manual-192.168.0.67",
            client_id="client-1",
            ack={"message": "joined", "joined_count": 1},
        )

        with patch("human_joiner.send_lobby_join", return_value=result) as send_lobby_join:
            code, output = self._run_cli(
                ["lobby", "--host", "192.168.0.67", "--player-name", "Tester"]
            )

        self.assertEqual(code, 0)
        self.assertIn("Accepted: True", output)
        send_lobby_join.assert_called_once()
        self.assertEqual(send_lobby_join.call_args.kwargs["player_name"], "Tester")

    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = human_joiner.main(argv)
        return code, stream.getvalue()


if __name__ == "__main__":
    unittest.main()
