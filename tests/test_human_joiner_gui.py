from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import human_joiner_gui
from human_joiner_config import HumanJoinerConfig
from sc2_join_launcher import PortCheck
from sc2_lan_discovery_client import (
    DEFAULT_HUMAN_CLIENT_PORT,
    DEFAULT_JOIN_PORT,
    DEFAULT_REMOTE_START_PORT,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LanRoom,
    LobbyJoinResult,
)
from sc2_map_downloader import MapSyncResult


def sample_room() -> LanRoom:
    return LanRoom(
        protocol=LAV_LAN_ROOM_PROTOCOL,
        version=LAV_LAN_ROOM_VERSION,
        source_id="source-1",
        room_id="room-1",
        room_name="LAV StarCraft II",
        preferred_bot="Changeling",
        preferred_map="IncorporealAIE_v4",
        proxy_host="26.189.202.71",
        proxy_ports=[5677, 5678],
        start_port=5690,
        join_port=DEFAULT_JOIN_PORT,
        human_client_port=DEFAULT_HUMAN_CLIENT_PORT,
        remote_start_port=DEFAULT_REMOTE_START_PORT,
    )


class HumanJoinerGuiTest(unittest.TestCase):
    def test_select_server_port_uses_requested_port(self) -> None:
        with patch("human_joiner_gui._can_bind_port") as can_bind_port:
            port = human_joiner_gui._select_server_port("127.0.0.1", 48000)

        self.assertEqual(port, 48000)
        can_bind_port.assert_not_called()

    def test_select_server_port_falls_forward_from_default_when_busy(self) -> None:
        def can_bind_port(_: str, port: int) -> bool:
            return port == human_joiner_gui.DEFAULT_GUI_PORT + 2

        with patch("human_joiner_gui._can_bind_port", side_effect=can_bind_port):
            port = human_joiner_gui._select_server_port("127.0.0.1", None)

        self.assertEqual(port, human_joiner_gui.DEFAULT_GUI_PORT + 2)

    def test_use_manual_host_builds_separate_result_and_preview(self) -> None:
        with patch("human_joiner_gui.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")):
            manual_result, preview, status, proxy_status, state = human_joiner_gui.use_manual_host(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
            )

        self.assertIn("Manual LAV StarCraft II target", manual_result)
        self.assertIn("Host: 26.189.202.71", manual_result)
        self.assertIn("Proxy ports: 5677,5678", manual_result)
        self.assertIn("SC2 launch command:", preview)
        self.assertIn("LAV_SC2_PROXY_HOST=26.189.202.71", preview)
        self.assertIn("LAV_SC2_PROXY_PORTS=5677", preview)
        self.assertIn("-listen 0.0.0.0 -port 5679", preview)
        self.assertIn("26.189.202.71:5678", preview)
        self.assertEqual(status, "Manual host target ready.")
        self.assertEqual(proxy_status, "")
        self.assertEqual(state["manual_room"].proxy_ports, [5677, 5678])

    def test_detect_sc2_path_returns_detected_path(self) -> None:
        with patch("human_joiner_gui.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")):
            path, status = human_joiner_gui.detect_sc2_path()

        self.assertEqual(path, r"C:\SC2\SC2_x64.exe")
        self.assertIn("Detected SC2_x64.exe", status)

    def test_save_gui_settings_writes_json_config(self) -> None:
        with patch("human_joiner_gui.save_config") as save_config:
            status = human_joiner_gui.save_gui_settings(
                r"D:\SC2\SC2_x64.exe",
                "10.0.0.5",
                "6000,6001",
                7000,
                7001,
            )

        config = save_config.call_args.args[0]
        self.assertIsInstance(config, HumanJoinerConfig)
        self.assertEqual(config.sc2_executable_path, r"D:\SC2\SC2_x64.exe")
        self.assertEqual(config.manual_host, "10.0.0.5")
        self.assertEqual(config.manual_proxy_ports, "6000,6001")
        self.assertEqual(config.manual_start_port, 7000)
        self.assertEqual(config.manual_join_port, 7001)
        self.assertIn("Saved settings", status)

    def test_load_gui_settings_reads_json_config(self) -> None:
        config = HumanJoinerConfig(
            sc2_executable_path=r"D:\SC2\SC2_x64.exe",
            manual_host="10.0.0.5",
            manual_proxy_ports="6000,6001",
            manual_start_port=7000,
            manual_join_port=7001,
        )

        with patch("human_joiner_gui.load_config", return_value=config):
            sc2_path, host, ports, start_port, join_port, status = human_joiner_gui.load_gui_settings()

        self.assertEqual(sc2_path, r"D:\SC2\SC2_x64.exe")
        self.assertEqual(host, "10.0.0.5")
        self.assertEqual(ports, "6000,6001")
        self.assertEqual(start_port, 7000)
        self.assertEqual(join_port, 7001)
        self.assertIn("Loaded settings", status)

    def test_join_manual_room_uses_settings_sc2_path(self) -> None:
        checks = [
            PortCheck("26.189.202.71", 5677, True),
            PortCheck("26.189.202.71", 5678, True),
        ]
        process = Mock(pid=1234)
        configured_path = Path(r"D:\Games\StarCraft II\Versions\Base100000\SC2_x64.exe")

        with (
            patch("human_joiner_gui.find_sc2_executable", return_value=None),
            patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path == configured_path),
            patch("human_joiner_gui.launch_sc2", return_value=process) as launch_sc2,
        ):
            _, preview, status, _, _ = human_joiner_gui.join_manual_room(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
                str(configured_path),
            )

        plan = launch_sc2.call_args.args[0]
        self.assertEqual(
            plan.command,
            [str(configured_path), "-listen", "0.0.0.0", "-port", "5679", "-displayMode", "0"],
        )
        self.assertIn(str(configured_path), preview)
        self.assertIn("StarCraft II launch requested. PID: 1234", status)

    def test_join_manual_room_rejects_missing_settings_sc2_path(self) -> None:
        checks = [
            PortCheck("26.189.202.71", 5677, True),
            PortCheck("26.189.202.71", 5678, True),
        ]

        with (
            patch("human_joiner_gui.find_sc2_executable", return_value=None),
            patch.object(Path, "is_file", autospec=True, return_value=False),
            patch("human_joiner_gui.check_proxy_ports", return_value=checks),
            patch("human_joiner_gui.launch_sc2") as launch_sc2,
        ):
            _, preview, status, _, _ = human_joiner_gui.join_manual_room(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
                r"D:\Missing\SC2_x64.exe",
            )

        self.assertIn("<SC2_x64.exe not found>", preview)
        self.assertIn("Set a valid SC2_x64.exe path in Settings", status)
        launch_sc2.assert_not_called()

    def test_scan_rooms_no_rooms_shows_manual_fallback(self) -> None:
        class FakeNoRoomsClient:
            def __init__(self, *, port: int) -> None:
                self.port = port

            def scan(self, *, duration_sec, diagnostics=None, **_) -> list:
                if diagnostics is not None:
                    diagnostics.start(bind_host="", port=self.port, duration_sec=duration_sec)
                    diagnostics.record_bind_success()
                    diagnostics.finish(rooms_returned=0)
                return []

        with (
            patch("human_joiner_gui.LanDiscoveryClient", FakeNoRoomsClient),
            patch("human_joiner_gui.find_sc2_executable", return_value=None),
        ):
            _, scan_result, preview, status, proxy_status, state = human_joiner_gui.scan_rooms(1, 47624)

        self.assertIn("No LAV StarCraft II rooms found", scan_result)
        self.assertIn("UDP bind: OK", scan_result)
        self.assertIn("UDP packets received: 0", scan_result)
        self.assertIn("Payload parse successes: 0", scan_result)
        self.assertIn("Payload parse failures: 0", scan_result)
        self.assertIn("Final rooms count: 0", scan_result)
        self.assertIn("UDP packet samples: none", scan_result)
        self.assertIn("Manual Host fallback", scan_result)
        self.assertIn("Host: 26.189.202.71", scan_result)
        self.assertIn("Proxy ports: 5677,5678", scan_result)
        self.assertIn("UDP broadcast may be blocked", scan_result)
        self.assertIn("26.189.202.71:5677", preview)
        self.assertIn("Check Manual Proxy", status)
        self.assertEqual(proxy_status, "")
        self.assertEqual(state["rooms"], [])

    def test_manual_proxy_check_probes_all_manual_ports(self) -> None:
        checks = [
            PortCheck("26.189.202.71", 5677, True),
            PortCheck("26.189.202.71", 5678, True),
        ]

        with (
            patch("human_joiner_gui.find_sc2_executable", return_value=None),
            patch("human_joiner_gui.check_proxy_ports", return_value=checks) as check_proxy_ports,
        ):
            _, _, status, proxy_status, _ = human_joiner_gui.check_manual_proxy(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
            )

        checked_room = check_proxy_ports.call_args.args[0]
        self.assertEqual(checked_room.proxy_ports, [5677, 5678])
        self.assertIn("Manual proxy check passed", status)
        self.assertIn("Join Manual Host can proceed", status)
        self.assertIn("OK 26.189.202.71:5677", proxy_status)
        self.assertIn("OK 26.189.202.71:5678", proxy_status)

    def test_scan_proxy_check_probes_all_scan_ports(self) -> None:
        room = sample_room()
        state = {"rooms": [room], "labels": ["room label"], "manual_room": None}

        with patch("human_joiner_gui.check_proxy_ports", return_value=[]) as check_proxy_ports:
            human_joiner_gui.check_selected_proxy("room label", state)

        checked_room = check_proxy_ports.call_args.args[0]
        self.assertEqual(checked_room.proxy_ports, [5677, 5678])

    def test_prepare_selected_sc2_waits_for_api_ready(self) -> None:
        room = sample_room()
        state = {"rooms": [room], "labels": ["room label"], "manual_room": None}
        result = {
            "ok": True,
            "pid": 1234,
            "human_client_port": DEFAULT_HUMAN_CLIENT_PORT,
            "port_ready": True,
            "api_ready": True,
            "api_ready_attempts": 4,
        }

        with (
            patch("human_joiner_gui._resolve_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")),
            patch.object(human_joiner_gui._REMOTE_START_SERVER, "prepare_sc2", return_value=result) as prepare_sc2,
        ):
            status, details = human_joiner_gui.prepare_selected_sc2(
                "room label",
                state,
                r"C:\SC2\SC2_x64.exe",
            )

        prepare_sc2.assert_called_once()
        self.assertEqual(room, prepare_sc2.call_args.args[0])
        self.assertIn("SC2 prepared", status)
        self.assertIn("Port ready: True", details)
        self.assertIn("API ready: True", details)
        self.assertIn("API ready attempts: 4", details)

    def test_join_selected_lobby_sends_lobby_join_request(self) -> None:
        room = sample_room()
        state = {"rooms": [room], "labels": ["room label"], "manual_room": None}
        result = LobbyJoinResult(
            ok=True,
            target_host="26.189.202.71",
            target_port=DEFAULT_JOIN_PORT,
            room_id="room-1",
            client_id="client-1",
            ack={"message": "joined", "joined_count": 1},
        )

        with (
            patch("human_joiner_gui.send_lobby_join", return_value=result) as send_lobby_join,
            patch("human_joiner_gui._start_remote_start_listener", return_value="Remote start listener\nListen: 0.0.0.0:47626") as start_listener,
        ):
            status, lobby_status, new_state = human_joiner_gui.join_selected_lobby(
                "room label",
                "Tester",
                state,
                r"C:\SC2\SC2_x64.exe",
            )

        send_lobby_join.assert_called_once()
        self.assertEqual(send_lobby_join.call_args.kwargs["player_name"], "Tester")
        start_listener.assert_called_once()
        self.assertIn("Lobby join accepted", status)
        self.assertIn("Map sync result", lobby_status)
        self.assertIn("Accepted: True", lobby_status)
        self.assertIn("Remote start listener", lobby_status)
        self.assertEqual(new_state["joined_room"], room)

    def test_join_selected_lobby_skips_duplicate_join_for_same_room(self) -> None:
        room = sample_room()
        state = {
            "rooms": [room],
            "labels": ["room label"],
            "manual_room": None,
            "joined_room": room,
            "lobby_join": LobbyJoinResult(
                ok=True,
                target_host="26.189.202.71",
                target_port=DEFAULT_JOIN_PORT,
                room_id="room-1",
                client_id="client-1",
            ),
        }

        with (
            patch("human_joiner_gui.send_lobby_join") as send_lobby_join,
            patch("human_joiner_gui._start_remote_start_listener", return_value="Remote start listener\nListen: 0.0.0.0:47626") as start_listener,
        ):
            status, lobby_status, new_state = human_joiner_gui.join_selected_lobby(
                "room label",
                "Tester",
                state,
                r"C:\SC2\SC2_x64.exe",
            )

        send_lobby_join.assert_not_called()
        start_listener.assert_called_once()
        self.assertIn("already joined", status)
        self.assertIn("Lobby join skipped", lobby_status)
        self.assertEqual(state, new_state)

    def test_join_selected_lobby_blocks_when_map_sync_fails(self) -> None:
        room = sample_room()
        room.map_file_name = "PersephoneLE.SC2Map"
        state = {"rooms": [room], "labels": ["room label"], "manual_room": None}

        with (
            patch(
                "human_joiner_gui.ensure_room_map_file",
                return_value=MapSyncResult(ok=False, error="map_write_permission_denied: denied"),
            ) as ensure_map,
            patch("human_joiner_gui.send_lobby_join") as send_lobby_join,
        ):
            status, lobby_status, new_state = human_joiner_gui.join_selected_lobby(
                "room label",
                "Tester",
                state,
                r"C:\SC2\SC2_x64.exe",
            )

        ensure_map.assert_called_once()
        send_lobby_join.assert_not_called()
        self.assertIn("Map sync failed", status)
        self.assertIn("map_write_permission_denied", lobby_status)
        self.assertEqual(state, new_state)

    def test_join_manual_room_launches_remote_human_listener_without_proxy_check(self) -> None:
        process = Mock(pid=1234)

        with (
            patch("human_joiner_gui.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")),
            patch("human_joiner_gui.launch_sc2", return_value=process) as launch_sc2,
        ):
            _, _, status, proxy_status, _ = human_joiner_gui.join_manual_room(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
            )

        plan = launch_sc2.call_args.args[0]
        self.assertEqual(plan.environment_overrides["LAV_SC2_PROXY_PORTS"], "5677")
        self.assertEqual(plan.command[-6:], ["-listen", "0.0.0.0", "-port", "5679", "-displayMode", "0"])
        self.assertIn("StarCraft II launch requested. PID: 1234", status)
        self.assertIn("Local listen: 0.0.0.0:5679", proxy_status)

    def test_join_manual_room_ignores_unreachable_proxy_for_remote_human_mode(self) -> None:
        checks = [PortCheck("26.189.202.71", 5677, False, "timed out")]
        process = Mock(pid=1234)

        with (
            patch("human_joiner_gui.find_sc2_executable", return_value=Path(r"C:\SC2\SC2_x64.exe")),
            patch("human_joiner_gui.check_proxy_ports", return_value=checks),
            patch("human_joiner_gui.launch_sc2", return_value=process) as launch_sc2,
        ):
            _, _, status, proxy_status, _ = human_joiner_gui.join_manual_room(
                "26.189.202.71",
                "5677,5678",
                5690,
                {},
            )

        self.assertIn("StarCraft II launch requested. PID: 1234", status)
        self.assertIn("Host proxy ports are diagnostic only", proxy_status)
        launch_sc2.assert_called_once()


if __name__ == "__main__":
    unittest.main()
