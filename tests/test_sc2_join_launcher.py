from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sc2_join_launcher import (
    build_environment_preview,
    build_launch_plan,
    check_proxy_ports,
    human_slot_room,
    launch_sc2,
)
from sc2_lan_discovery_client import LanRoom


def sample_room() -> LanRoom:
    return LanRoom(
        protocol="lav.sc2.lan_room",
        version=1,
        source_id="source-1",
        room_id="room-1",
        room_name="LAV StarCraft II",
        preferred_bot="Changeling",
        preferred_map="IncorporealAIE_v4",
        proxy_host="192.168.0.67",
        proxy_ports=[5677, 5678],
        start_port=5690,
        human_client_port=5679,
    )


class Sc2JoinLauncherTest(unittest.TestCase):
    def test_build_environment_preview(self) -> None:
        env = build_environment_preview(sample_room())

        self.assertEqual(env["LAV_SC2_PROXY_HOST"], "192.168.0.67")
        self.assertEqual(env["LAV_SC2_PROXY_PORTS"], "5677,5678")
        self.assertEqual(env["LAV_SC2_ROOM_ID"], "room-1")
        self.assertEqual(env["LAV_SC2_SOURCE_ID"], "source-1")
        self.assertEqual(env["LAV_SC2_START_PORT"], "5690")
        self.assertEqual(env["LAV_SC2_HUMAN_CLIENT_HOST"], "0.0.0.0")
        self.assertEqual(env["LAV_SC2_HUMAN_CLIENT_PORT"], "5679")

    def test_build_environment_preview_uses_loopback_sender_for_same_pc_scan(self) -> None:
        room = sample_room()
        room.proxy_host = "26.189.202.71"
        room.sender_ip = "127.0.0.1"

        env = build_environment_preview(room)

        self.assertEqual(env["LAV_SC2_PROXY_HOST"], "127.0.0.1")

    def test_build_environment_preview_ignores_wildcard_proxy_host(self) -> None:
        room = sample_room()
        room.proxy_host = "0.0.0.0"
        room.sender_ip = "192.168.0.67"

        env = build_environment_preview(room)

        self.assertEqual(env["LAV_SC2_PROXY_HOST"], "192.168.0.67")

    def test_build_launch_plan_uses_executable_and_env(self) -> None:
        executable = Path(r"C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe")

        plan = build_launch_plan(sample_room(), executable)

        self.assertEqual(
            plan.command,
            [str(executable), "-listen", "0.0.0.0", "-port", "5679", "-displayMode", "0"],
        )
        self.assertEqual(plan.environment_overrides["LAV_SC2_PROXY_HOST"], "192.168.0.67")
        self.assertEqual(
            plan.environment_overrides["SC2PATH"],
            r"C:\Program Files (x86)\StarCraft II",
        )
        self.assertEqual(
            plan.working_directory,
            r"C:\Program Files (x86)\StarCraft II\Versions\Base97425",
        )
        self.assertEqual(
            plan.path_prepend,
            [
                r"C:\Program Files (x86)\StarCraft II\Support64",
                r"C:\Program Files (x86)\StarCraft II\Versions\Base97425",
            ],
        )

    def test_human_slot_room_uses_first_proxy_port(self) -> None:
        room = human_slot_room(sample_room())

        self.assertEqual(room.proxy_ports, [5677])

    def test_launch_sc2_merges_environment(self) -> None:
        process = Mock(pid=1234)
        plan = build_launch_plan(
            sample_room(),
            Path(r"C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe"),
        )

        with patch("sc2_join_launcher.subprocess.Popen", return_value=process) as popen:
            returned = launch_sc2(plan, base_env={"KEEP": "1", "PATH": r"C:\Existing"})

        self.assertIs(returned, process)
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"]["KEEP"], "1")
        self.assertEqual(kwargs["env"]["LAV_SC2_PROXY_PORTS"], "5677,5678")
        self.assertEqual(kwargs["env"]["SC2PATH"], r"C:\Program Files (x86)\StarCraft II")
        self.assertEqual(
            kwargs["env"]["PATH"],
            (
                r"C:\Program Files (x86)\StarCraft II\Support64;"
                r"C:\Program Files (x86)\StarCraft II\Versions\Base97425;"
                r"C:\Existing"
            ),
        )
        self.assertEqual(
            kwargs["cwd"],
            r"C:\Program Files (x86)\StarCraft II\Versions\Base97425",
        )

    def test_check_proxy_ports_reports_socket_results(self) -> None:
        room = sample_room()

        with patch("sc2_join_launcher.socket.create_connection") as create_connection:
            create_connection.return_value.__enter__.return_value = object()
            checks = check_proxy_ports(room)

        self.assertEqual([check.reachable for check in checks], [True, True])
        self.assertEqual([check.port for check in checks], [5677, 5678])

    def test_check_proxy_ports_never_connects_to_wildcard_host(self) -> None:
        room = sample_room()
        room.proxy_host = "0.0.0.0"
        room.sender_ip = "192.168.0.67"

        with patch("sc2_join_launcher.socket.create_connection") as create_connection:
            create_connection.return_value.__enter__.return_value = object()
            checks = check_proxy_ports(room)

        self.assertEqual([check.host for check in checks], ["192.168.0.67", "192.168.0.67"])
        create_connection.assert_any_call(("192.168.0.67", 5677), timeout=1.0)


if __name__ == "__main__":
    unittest.main()
