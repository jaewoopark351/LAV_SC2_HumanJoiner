from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from sc2_lan_discovery_client import DEFAULT_HUMAN_CLIENT_PORT, LanRoom, select_room_connect_host
from sc2_path_finder import find_sc2_executable


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    environment_overrides: dict[str, str]
    working_directory: str = ""
    path_prepend: list[str] | None = None


@dataclass(frozen=True)
class PortCheck:
    host: str
    port: int
    reachable: bool
    error: str = ""


def build_command_preview(sc2_executable: Path | None, room: LanRoom | None = None) -> str:
    if sc2_executable is None:
        return "<SC2_x64.exe not found>"
    command = [str(sc2_executable)]
    if room is not None:
        command.extend(_sc2_api_listen_args(room))
    return " ".join(_quote_for_display(part) for part in command)


def build_environment_preview(room: LanRoom) -> dict[str, str]:
    env = {
        "LAV_SC2_PROXY_HOST": select_room_connect_host(room),
        "LAV_SC2_PROXY_PORTS": ",".join(str(port) for port in room.proxy_ports),
        "LAV_SC2_ROOM_ID": room.room_id,
        "LAV_SC2_SOURCE_ID": room.source_id,
    }
    if room.start_port is not None:
        env["LAV_SC2_START_PORT"] = str(room.start_port)
    env["LAV_SC2_HUMAN_CLIENT_HOST"] = "0.0.0.0"
    env["LAV_SC2_HUMAN_CLIENT_PORT"] = str(human_client_port(room))
    return env


def human_slot_room(room: LanRoom) -> LanRoom:
    if len(room.proxy_ports) <= 1:
        return room
    return replace(room, proxy_ports=[room.proxy_ports[0]])


def build_launch_plan(room: LanRoom, sc2_executable: Path | None = None) -> LaunchPlan:
    executable = find_sc2_executable() if sc2_executable is None else sc2_executable
    if executable is None:
        raise FileNotFoundError("SC2_x64.exe was not found")
    runtime = _sc2_runtime_paths(executable)
    return LaunchPlan(
        command=[str(executable), *_sc2_api_listen_args(room)],
        environment_overrides={
            **build_environment_preview(room),
            **runtime["environment_overrides"],
        },
        working_directory=runtime["working_directory"],
        path_prepend=runtime["path_prepend"],
    )


def human_client_port(room: LanRoom) -> int:
    port = room.human_client_port or DEFAULT_HUMAN_CLIENT_PORT
    try:
        parsed = int(port)
    except (TypeError, ValueError):
        return DEFAULT_HUMAN_CLIENT_PORT
    if parsed <= 0 or parsed > 65535:
        return DEFAULT_HUMAN_CLIENT_PORT
    return parsed


def launch_sc2(
    plan: LaunchPlan,
    *,
    base_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(plan.environment_overrides)
    path_prepend = [str(item) for item in (plan.path_prepend or []) if str(item)]
    if path_prepend:
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*path_prepend, existing_path] if existing_path else path_prepend)
    cwd = plan.working_directory or None
    return subprocess.Popen(plan.command, env=env, cwd=cwd)


def check_proxy_ports(room: LanRoom, *, timeout_sec: float = 1.0) -> list[PortCheck]:
    host = select_room_connect_host(room)
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


def _sc2_runtime_paths(executable: Path) -> dict[str, object]:
    #20260711_kpopmodder: SC2_x64.exe needs Support64 and the active Base folder on PATH when launched directly.
    executable = Path(executable)
    base_dir = executable.parent
    sc2_root = _infer_sc2_root_from_base_dir(base_dir)
    path_prepend = [str(base_dir)]
    environment_overrides: dict[str, str] = {}
    if sc2_root is not None:
        support64 = sc2_root / "Support64"
        path_prepend = [str(support64), str(base_dir)]
        environment_overrides["SC2PATH"] = str(sc2_root)
    return {
        "working_directory": str(base_dir),
        "path_prepend": _dedupe_paths(path_prepend),
        "environment_overrides": environment_overrides,
    }


def _infer_sc2_root_from_base_dir(base_dir: Path) -> Path | None:
    if not base_dir.name.lower().startswith("base"):
        return None
    versions_dir = base_dir.parent
    if versions_dir.name.lower() != "versions":
        return None
    return versions_dir.parent


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        normalized = os.path.normcase(os.path.normpath(str(path)))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(str(path))
    return result


def _sc2_api_listen_args(room: LanRoom) -> list[str]:
    #20260709_kpopmodder: Remote human mode needs the joining PC to expose its SC2 API port for the LAV host proxy.
    return ["-listen", "0.0.0.0", "-port", str(human_client_port(room)), "-displayMode", "0"]
