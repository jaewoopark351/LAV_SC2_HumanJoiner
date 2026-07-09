from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sc2_join_launcher import (
    PortCheck,
    build_command_preview,
    build_environment_preview,
    build_launch_plan,
    check_proxy_ports,
    human_slot_room,
    launch_sc2,
)
from human_joiner_logging import setup_logging
from sc2_lan_discovery_client import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_JOIN_PORT,
    DEFAULT_SCAN_SECONDS,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LanDiscoveryClient,
    LanScanDiagnostics,
    LanRoom,
    LobbyJoinResult,
    send_lobby_join,
)
from sc2_path_finder import find_sc2_executable


DEFAULT_MANUAL_HOST = "26.189.202.71"
DEFAULT_MANUAL_PROXY_PORTS = "5677,5678"
DEFAULT_MANUAL_START_PORT = 5690
DEFAULT_MANUAL_JOIN_PORT = DEFAULT_JOIN_PORT

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_path = setup_logging("human_joiner")
    logger.info("CLI started; log_path=%s", log_path)

    if args.command == "scan":
        return scan_command(args)
    if args.command == "check":
        return check_command(args)
    if args.command == "join":
        return join_command(args)
    if args.command == "lobby":
        return lobby_command(args)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LAV StarCraft II Human Joiner")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="scan LAN for LAV StarCraft II rooms")
    scan_parser.add_argument("--seconds", type=float, default=DEFAULT_SCAN_SECONDS, help="scan duration")
    scan_parser.add_argument("--port", type=int, default=DEFAULT_DISCOVERY_PORT, help="UDP discovery port")
    scan_parser.add_argument(
        "--debug",
        action="store_true",
        help="log raw UDP payloads and parse results while scanning",
    )

    check_parser = subparsers.add_parser("check", help="scan or target a host and check proxy ports")
    _add_room_source_arguments(check_parser)

    join_parser = subparsers.add_parser("join", help="launch StarCraft II for a selected LAV room")
    _add_room_source_arguments(join_parser)
    join_parser.add_argument(
        "--skip-proxy-check",
        action="store_true",
        help="launch even when proxy ports have not been checked first",
    )

    lobby_parser = subparsers.add_parser("lobby", help="send a lobby join request without launching StarCraft II")
    _add_room_source_arguments(lobby_parser)
    lobby_parser.add_argument("--player-name", default="Human", help="player name shown to the LAV host")

    return parser


def _add_room_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seconds", type=float, default=DEFAULT_SCAN_SECONDS, help="scan duration")
    parser.add_argument("--port", type=int, default=DEFAULT_DISCOVERY_PORT, help="UDP discovery port")
    parser.add_argument("--room-index", type=int, default=1, help="1-based room index from the scan result")
    parser.add_argument("--host", default="", help="manual LAV host IP or hostname; skips UDP scan")
    parser.add_argument("--proxy-ports", default=DEFAULT_MANUAL_PROXY_PORTS, help="manual proxy ports when --host is used")
    parser.add_argument("--start-port", type=int, default=DEFAULT_MANUAL_START_PORT, help="manual start port metadata")
    parser.add_argument("--join-port", type=int, default=None, help="manual lobby join UDP port metadata")
    parser.add_argument(
        "--check-all-ports",
        action="store_true",
        help="check every advertised proxy port instead of only the first human slot port",
    )


def scan_command(args: argparse.Namespace) -> int:
    logger.info("Scan started; port=%s duration_sec=%s", args.port, args.seconds)
    client = LanDiscoveryClient(port=args.port)
    diagnostics = LanScanDiagnostics()
    try:
        rooms = client.scan(
            duration_sec=args.seconds,
            diagnostics=diagnostics,
            debug_logger=logger if bool(getattr(args, "debug", False)) else None,
            log_payloads=bool(getattr(args, "debug", False)),
        )
    except OSError as exc:
        diagnostics.log_summary(logger, "CLI scan")
        logger.exception("Scan failed; port=%s duration_sec=%s", args.port, args.seconds)
        if bool(getattr(args, "debug", False)):
            _print_scan_debug_report(diagnostics)
        print(f"Scan LAN failed on UDP port {args.port}: {exc}")
        _print_manual_fallback_hint()
        return 1
    diagnostics.log_summary(logger, "CLI scan")
    if bool(getattr(args, "debug", False)):
        _print_scan_debug_report(diagnostics)
    sc2_executable = find_sc2_executable()
    logger.info("Scan completed; rooms=%s sc2_executable=%s", len(rooms), sc2_executable or "not found")

    if not rooms:
        print(f"No LAV StarCraft II rooms found on UDP port {args.port}.")
        _print_sc2_status(sc2_executable)
        _print_manual_fallback_hint()
        return 1

    for index, room in enumerate(rooms):
        if index:
            print()
        print_room(room, sc2_executable)

    return 0


def check_command(args: argparse.Namespace) -> int:
    room = resolve_room(args)
    if room is None:
        return 1

    sc2_executable = find_sc2_executable()
    check_room = room if bool(args.check_all_ports) else human_slot_room(room)
    print_room(check_room, sc2_executable)
    checks = check_proxy_ports(check_room)
    print()
    print("Proxy check")
    print(format_port_checks(checks))
    return 0 if checks and all(check.reachable for check in checks) else 2


def join_command(args: argparse.Namespace) -> int:
    room = resolve_room(args)
    if room is None:
        return 1

    sc2_executable = find_sc2_executable()
    if sc2_executable is None:
        print("SC2_x64.exe was not found. Install StarCraft II or add a path finder fallback first.")
        return 3

    if not bool(args.skip_proxy_check):
        join_room = human_slot_room(room)
        checks = check_proxy_ports(join_room)
        print("Proxy check")
        print(format_port_checks(checks))
        if not checks or not all(check.reachable for check in checks):
            print("Join aborted because one or more proxy ports are unreachable.")
            return 2

    plan = build_launch_plan(human_slot_room(room), sc2_executable)
    process = launch_sc2(plan)
    logger.info("Join launch process started; pid=%s command=%s", process.pid, plan.command)
    print(f"StarCraft II launch requested. PID: {process.pid}")
    return 0


def lobby_command(args: argparse.Namespace) -> int:
    room = resolve_room(args)
    if room is None:
        return 1

    result = send_lobby_join(
        room,
        player_name=str(getattr(args, "player_name", "Human") or "Human"),
        target_port=getattr(args, "join_port", None),
    )
    print(format_lobby_join_result(result))
    return 0 if result.ok else 4


def print_room(room: LanRoom, sc2_executable: Path | None) -> None:
    logger.info(
        "Room found; room_id=%s source_id=%s host=%s ports=%s bot=%s map=%s",
        room.room_id,
        room.source_id,
        room.proxy_host or room.sender_ip or room.host_name,
        ",".join(str(port) for port in room.proxy_ports),
        room.preferred_bot,
        room.preferred_map,
    )
    print("Found LAV StarCraft II room")
    print(f"Host: {room.proxy_host or room.sender_ip or room.host_name}")
    print(f"Bot: {room.preferred_bot}")
    print(f"Map: {room.preferred_map}")
    print(f"Proxy ports: {','.join(str(port) for port in room.proxy_ports)}")
    print(f"Join port: {room.join_port or DEFAULT_JOIN_PORT}")
    _print_sc2_status(sc2_executable)

    print()
    print("Launch preview")
    command = build_command_preview(sc2_executable)
    print(f"Command: {command}")
    print("Environment:")
    for name, value in build_environment_preview(room).items():
        print(f"  {name}={value}")


def resolve_room(args: argparse.Namespace) -> LanRoom | None:
    manual_host = str(getattr(args, "host", "") or "").strip()
    if manual_host:
        try:
            ports = parse_proxy_ports(getattr(args, "proxy_ports", DEFAULT_MANUAL_PROXY_PORTS))
        except ValueError as exc:
            print(f"Invalid --proxy-ports value: {exc}")
            return None
        return LanRoom(
            protocol=LAV_LAN_ROOM_PROTOCOL,
            version=LAV_LAN_ROOM_VERSION,
            source_id="manual",
            room_id=f"manual-{manual_host}",
            room_name="Manual LAV StarCraft II",
            preferred_bot="",
            preferred_map="",
            proxy_host=manual_host,
            proxy_ports=ports,
            start_port=int(getattr(args, "start_port", DEFAULT_MANUAL_START_PORT) or DEFAULT_MANUAL_START_PORT),
            join_port=int(getattr(args, "join_port", DEFAULT_MANUAL_JOIN_PORT) or DEFAULT_MANUAL_JOIN_PORT),
        )

    duration = _safe_float(getattr(args, "seconds", DEFAULT_SCAN_SECONDS), DEFAULT_SCAN_SECONDS)
    port = int(getattr(args, "port", DEFAULT_DISCOVERY_PORT) or DEFAULT_DISCOVERY_PORT)
    diagnostics = LanScanDiagnostics()
    try:
        rooms = LanDiscoveryClient(port=port).scan(duration_sec=duration, diagnostics=diagnostics)
    except OSError as exc:
        diagnostics.log_summary(logger, "CLI room resolve scan")
        print(f"Scan LAN failed on UDP port {port}: {exc}")
        _print_manual_fallback_hint()
        return None
    diagnostics.log_summary(logger, "CLI room resolve scan")
    if not rooms:
        print(f"No LAV StarCraft II rooms found on UDP port {port}.")
        _print_manual_fallback_hint()
        return None

    room_index = int(getattr(args, "room_index", 1) or 1)
    selected_index = room_index - 1
    if selected_index < 0 or selected_index >= len(rooms):
        print(f"Room index {room_index} is out of range. Found {len(rooms)} room(s).")
        return None
    room = rooms[selected_index]
    if getattr(args, "join_port", None):
        room.join_port = int(args.join_port)
    return room


def parse_proxy_ports(value: object) -> list[int]:
    raw_items = str(value or "").split(",")
    ports: list[int] = []
    for raw_item in raw_items:
        text = raw_item.strip()
        if not text:
            continue
        try:
            port = int(text)
        except ValueError as exc:
            raise ValueError(f"{text!r} is not an integer") from exc
        if port <= 0 or port > 65535:
            raise ValueError(f"{port} is outside 1-65535")
        if port not in ports:
            ports.append(port)
    if not ports:
        raise ValueError("at least one port is required")
    return ports


def format_port_checks(checks: list[PortCheck]) -> str:
    lines: list[str] = []
    for check in checks:
        if check.reachable:
            lines.append(f"OK {check.host}:{check.port}")
        else:
            target = f"{check.host}:{check.port}" if check.port else check.host
            lines.append(f"FAILED {target} {check.error}".rstrip())
    return "\n".join(lines)


def format_lobby_join_result(result: LobbyJoinResult) -> str:
    lines = [
        "Lobby join result",
        f"Target: {result.target_host}:{result.target_port}",
        f"Room: {result.room_id}",
        f"Client: {result.client_id}",
        f"Accepted: {result.ok}",
    ]
    message = result.ack.get("message") if isinstance(result.ack, dict) else ""
    if message:
        lines.append(f"Message: {message}")
    joined_count = result.ack.get("joined_count") if isinstance(result.ack, dict) else None
    if joined_count is not None:
        lines.append(f"Joined count: {joined_count}")
    if result.error:
        lines.append(f"Error: {result.error}")
    return "\n".join(lines)


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _print_sc2_status(sc2_executable: Path | None) -> None:
    if sc2_executable is None:
        print("SC2 executable: not found")
        return
    print(f"SC2 executable: {sc2_executable}")


def _print_manual_fallback_hint() -> None:
    print()
    print("Manual host fallback is available even when UDP discovery finds 0 rooms.")
    print(f"Default manual host: {DEFAULT_MANUAL_HOST}")
    print(f"Default proxy ports: {DEFAULT_MANUAL_PROXY_PORTS}")
    print(
        "Check manually: "
        f"python human_joiner.py check --host {DEFAULT_MANUAL_HOST} "
        f"--proxy-ports {DEFAULT_MANUAL_PROXY_PORTS} --check-all-ports"
    )


def _print_scan_debug_report(diagnostics: LanScanDiagnostics) -> None:
    print()
    print("UDP scan debug")
    print(f"Bind host: {diagnostics.bind_host or '0.0.0.0'}")
    print(f"Port: {diagnostics.port}")
    print(f"Duration seconds: {diagnostics.duration_sec}")
    print(f"Bind: {'OK' if diagnostics.bind_succeeded else 'FAILED'}")
    if diagnostics.bind_error:
        print(f"Bind error: {diagnostics.bind_error}")
    print(f"Started: {diagnostics.started_at}")
    print(f"Ended: {diagnostics.ended_at}")
    print(f"UDP packets received: {diagnostics.packets_received}")
    print(f"Parse success count: {diagnostics.parsed_rooms}")
    print(f"Parse failed count: {diagnostics.parse_failures}")
    print(f"Parse failure reasons: {_format_counts(diagnostics.parse_failure_reasons)}")
    print(f"Sender IPs: {_format_counts(diagnostics.sender_counts)}")
    print(f"Final rooms count: {diagnostics.rooms_returned}")
    if diagnostics.packet_events:
        print("UDP packet events:")
        for index, event in enumerate(diagnostics.packet_events, start=1):
            print(
                f"  #{index} sender={event.sender_ip}:{event.sender_port} "
                f"bytes={event.byte_count} parse={event.parse_result}"
            )
            print(f"     raw={event.raw_payload}")
    else:
        print("UDP packet events: none")


def _format_counts(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


if __name__ == "__main__":
    raise SystemExit(main())
