from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from sc2_lan_discovery_client import LanRoom
from sc2_path_finder import find_sc2_executable


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    environment_overrides: dict[str, str]


@dataclass(frozen=True)
class PortCheck:
    host: str
    port: int
    reachable: bool
    error: str = ""


def build_command_preview(sc2_executable: Path | None) -> str:
    if sc2_executable is None:
        return "<SC2_x64.exe not found>"
    return _quote_for_display(str(sc2_executable))


def build_environment_preview(room: LanRoom) -> dict[str, str]:
    env = {
        "LAV_SC2_PROXY_HOST": room.proxy_host or room.sender_ip,
        "LAV_SC2_PROXY_PORTS": ",".join(str(port) for port in room.proxy_ports),
        "LAV_SC2_ROOM_ID": room.room_id,
        "LAV_SC2_SOURCE_ID": room.source_id,
    }
    if room.start_port is not None:
        env["LAV_SC2_START_PORT"] = str(room.start_port)
    return env


def human_slot_room(room: LanRoom) -> LanRoom:
    if len(room.proxy_ports) <= 1:
        return room
    return replace(room, proxy_ports=[room.proxy_ports[0]])


def build_launch_plan(room: LanRoom, sc2_executable: Path | None = None) -> LaunchPlan:
    executable = find_sc2_executable() if sc2_executable is None else sc2_executable
    if executable is None:
        raise FileNotFoundError("SC2_x64.exe was not found")
    return LaunchPlan(command=[str(executable)], environment_overrides=build_environment_preview(room))


def launch_sc2(
    plan: LaunchPlan,
    *,
    base_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(plan.environment_overrides)
    return subprocess.Popen(plan.command, env=env)


def check_proxy_ports(room: LanRoom, *, timeout_sec: float = 1.0) -> list[PortCheck]:
    host = room.proxy_host or room.sender_ip
    if not host:
        return [PortCheck(host="", port=0, reachable=False, error="proxy host is missing")]

    checks: list[PortCheck] = []
    for port in room.proxy_ports:
        checks.append(check_tcp_port(host, port, timeout_sec=timeout_sec))
    return checks


def check_tcp_port(host: str, port: int, *, timeout_sec: float = 1.0) -> PortCheck:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return PortCheck(host=host, port=port, reachable=True)
    except OSError as exc:
        return PortCheck(host=host, port=port, reachable=False, error=str(exc))


def _quote_for_display(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value) or '"' in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value
