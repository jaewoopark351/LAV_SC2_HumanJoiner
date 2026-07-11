from __future__ import annotations

import argparse
import logging
import socket
from pathlib import Path
from typing import Any

from human_joiner import parse_proxy_ports
from human_joiner_config import (
    CONFIG_PATH,
    DEFAULT_MANUAL_HOST,
    DEFAULT_MANUAL_JOIN_PORT,
    DEFAULT_MANUAL_PROXY_PORTS,
    DEFAULT_MANUAL_START_PORT,
    HumanJoinerConfig,
    load_config,
    save_config,
)
from human_joiner_logging import setup_logging
from sc2_join_launcher import (
    PortCheck,
    build_command_preview,
    build_environment_preview,
    build_launch_plan,
    check_proxy_ports,
    human_slot_room,
    launch_sc2,
)
from sc2_lan_discovery_client import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_JOIN_PORT,
    DEFAULT_HUMAN_CLIENT_PORT,
    DEFAULT_REMOTE_START_PORT,
    DEFAULT_MAP_DOWNLOAD_PORT,
    DEFAULT_SCAN_SECONDS,
    LAV_LAN_ROOM_PROTOCOL,
    LAV_LAN_ROOM_VERSION,
    LanDiscoveryClient,
    LanScanDiagnostics,
    LanRoom,
    LobbyJoinResult,
    select_room_connect_host,
    send_lobby_join,
)
from sc2_map_downloader import MapSyncResult, ensure_room_map_file
from sc2_path_finder import find_sc2_executable
from sc2_remote_start_server import DEFAULT_SC2_API_READY_TIMEOUT_SEC, RemoteHumanStartServer


INSTALL_HELP = (
    "Gradio is not installed. Install it in this project's virtual environment:\n"
    "  .venv\\Scripts\\activate\n"
    "  python -m pip install -r requirements-gradio.txt\n"
)
DEFAULT_GUI_HOST = "127.0.0.1"
DEFAULT_GUI_PORT = 47860

logger = logging.getLogger(__name__)
_REMOTE_START_SERVER = RemoteHumanStartServer(port=DEFAULT_REMOTE_START_PORT, logger=logger)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LAV StarCraft II Human Joiner GUI")
    parser.add_argument("--host", default=DEFAULT_GUI_HOST, help="Gradio server host")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Gradio server port; defaults to the first open port from {DEFAULT_GUI_PORT}",
    )
    parser.add_argument("--share", action="store_true", help="enable Gradio share link")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser automatically")
    args = parser.parse_args(argv)
    log_path = setup_logging("human_joiner_gui")
    server_port = _select_server_port(args.host, args.port)
    logger.info("GUI starting; host=%s port=%s share=%s log_path=%s", args.host, server_port, args.share, log_path)
    if args.port is None and server_port != DEFAULT_GUI_PORT:
        print(f"Default GUI port {DEFAULT_GUI_PORT} is busy. Using {server_port} instead.")

    try:
        import gradio as gr
    except ImportError:
        logger.exception("Gradio is not installed")
        print(INSTALL_HELP)
        return 1

    app = build_app(gr)
    app.launch(
        server_name=args.host,
        server_port=server_port,
        share=args.share,
        inbrowser=not args.no_browser,
    )
    return 0


def _select_server_port(host: str, requested_port: int | None) -> int:
    if requested_port is not None:
        return requested_port
    return _first_available_port(host, DEFAULT_GUI_PORT)


def _first_available_port(host: str, start_port: int, *, max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        if _can_bind_port(host, port):
            return port
    raise OSError(f"Cannot find empty port in range: {start_port}-{start_port + max_attempts - 1}.")


def _can_bind_port(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def build_app(gr: Any) -> Any:
    saved_config = load_config()

    with gr.Blocks(title="LAV SC2 Human Joiner") as app:
        gr.Markdown("# LAV SC2 Human Joiner")

        state = gr.State({"rooms": [], "labels": [], "manual_room": None, "joined_room": None})

        with gr.Tabs():
            with gr.Tab("Game Play"):
                with gr.Row():
                    scan_seconds = gr.Number(value=DEFAULT_SCAN_SECONDS, label="Scan seconds", precision=1)
                    discovery_port = gr.Number(value=DEFAULT_DISCOVERY_PORT, label="UDP discovery port", precision=0)
                    lobby_player_name = gr.Textbox(value=socket.gethostname(), label="Lobby player name")

                with gr.Row():
                    scan_button = gr.Button("Scan LAN", variant="primary")
                    join_lobby_button = gr.Button("Join Lobby", variant="primary")
                    prepare_sc2_button = gr.Button("Prepare SC2")
                    check_button = gr.Button("Check Proxy")
                    join_button = gr.Button("Join Game", variant="primary")

                room_choice = gr.Dropdown(label="LAV room", choices=[], interactive=True)
                scan_result = gr.Textbox(label="Scan LAN result", lines=8, interactive=False)
                scan_proxy_status = gr.Textbox(label="Scan LAN proxy check", lines=4, interactive=False)
                lobby_status = gr.Textbox(label="Lobby join status", lines=4, interactive=False)

                with gr.Row():
                    manual_host = gr.Textbox(value=saved_config.manual_host, label="Manual host")
                    manual_proxy_ports = gr.Textbox(value=saved_config.manual_proxy_ports, label="Manual proxy ports")
                    manual_start_port = gr.Number(value=saved_config.manual_start_port, label="Manual start port", precision=0)
                    manual_join_port = gr.Number(value=saved_config.manual_join_port, label="Manual join port", precision=0)

                with gr.Row():
                    manual_button = gr.Button("Use Manual Host")
                    manual_lobby_button = gr.Button("Join Manual Lobby")
                    manual_check_button = gr.Button("Check Manual Proxy")
                    manual_join_button = gr.Button("Join Manual Host", variant="primary")

                manual_result = gr.Textbox(label="Manual host result", lines=8, interactive=False)
                manual_proxy_status = gr.Textbox(label="Manual host proxy check", lines=4, interactive=False)
                launch_preview = gr.Textbox(label="SC2 launch / connection preview", lines=10, interactive=False)
                status = gr.Textbox(label="Status", interactive=False)

            with gr.Tab("Settings"):
                sc2_executable_path = gr.Textbox(
                    value=_default_sc2_path_text(saved_config),
                    label="SC2_x64.exe path",
                )
                with gr.Row():
                    detect_sc2_button = gr.Button("Detect SC2 Path")
                    load_settings_button = gr.Button("Load Settings")
                    save_settings_button = gr.Button("Save Settings", variant="primary")
                settings_status = gr.Textbox(label="Settings status", interactive=False)

        scan_button.click(
            fn=scan_rooms,
            inputs=[scan_seconds, discovery_port, sc2_executable_path],
            outputs=[room_choice, scan_result, launch_preview, status, scan_proxy_status, state],
        )
        room_choice.change(
            fn=select_room,
            inputs=[room_choice, state, sc2_executable_path],
            outputs=[scan_result, launch_preview, status, scan_proxy_status],
        )
        check_button.click(
            fn=check_selected_proxy,
            inputs=[room_choice, state],
            outputs=[scan_proxy_status],
        )
        join_lobby_button.click(
            fn=join_selected_lobby,
            inputs=[room_choice, lobby_player_name, state, sc2_executable_path],
            outputs=[status, lobby_status, state],
        )
        prepare_sc2_button.click(
            fn=prepare_selected_sc2,
            inputs=[room_choice, state, sc2_executable_path],
            outputs=[status, scan_proxy_status],
        )
        join_button.click(
            fn=join_selected_room,
            inputs=[room_choice, state, sc2_executable_path],
            outputs=[status, scan_proxy_status],
        )
        manual_button.click(
            fn=use_manual_host,
            inputs=[manual_host, manual_proxy_ports, manual_start_port, state, sc2_executable_path],
            outputs=[manual_result, launch_preview, status, manual_proxy_status, state],
        )
        manual_check_button.click(
            fn=check_manual_proxy,
            inputs=[manual_host, manual_proxy_ports, manual_start_port, state, sc2_executable_path],
            outputs=[manual_result, launch_preview, status, manual_proxy_status, state],
        )
        manual_lobby_button.click(
            fn=join_manual_lobby,
            inputs=[
                manual_host,
                manual_proxy_ports,
                manual_start_port,
                manual_join_port,
                lobby_player_name,
                state,
                sc2_executable_path,
            ],
            outputs=[manual_result, launch_preview, status, lobby_status, state],
        )
        manual_join_button.click(
            fn=join_manual_room,
            inputs=[manual_host, manual_proxy_ports, manual_start_port, state, sc2_executable_path],
            outputs=[manual_result, launch_preview, status, manual_proxy_status, state],
        )
        detect_sc2_button.click(
            fn=detect_sc2_path,
            outputs=[sc2_executable_path, settings_status],
        )
        load_settings_button.click(
            fn=load_gui_settings,
            outputs=[
                sc2_executable_path,
                manual_host,
                manual_proxy_ports,
                manual_start_port,
                manual_join_port,
                settings_status,
            ],
        )
        save_settings_button.click(
            fn=save_gui_settings,
            inputs=[
                sc2_executable_path,
                manual_host,
                manual_proxy_ports,
                manual_start_port,
                manual_join_port,
            ],
            outputs=[settings_status],
        )

    return app


def detect_sc2_path() -> tuple[str, str]:
    executable = find_sc2_executable()
    if executable is None:
        return "", "SC2_x64.exe was not found."
    return str(executable), f"Detected SC2_x64.exe: {executable}"


def load_gui_settings() -> tuple[str, str, str, int, int, str]:
    config = load_config()
    return (
        _default_sc2_path_text(config),
        config.manual_host,
        config.manual_proxy_ports,
        config.manual_start_port,
        config.manual_join_port,
        f"Loaded settings from {CONFIG_PATH}",
    )


def save_gui_settings(
    sc2_path: object,
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    manual_join_port: object,
) -> str:
    config = HumanJoinerConfig(
        sc2_executable_path=str(sc2_path or "").strip(),
        manual_host=str(manual_host or "").strip() or DEFAULT_MANUAL_HOST,
        manual_proxy_ports=str(manual_proxy_ports or "").strip() or DEFAULT_MANUAL_PROXY_PORTS,
        manual_start_port=_safe_int(manual_start_port, DEFAULT_MANUAL_START_PORT),
        manual_join_port=_safe_int(manual_join_port, DEFAULT_MANUAL_JOIN_PORT),
    )
    path = save_config(config)
    return f"Saved settings to {path}"


def _default_sc2_path_text(config: HumanJoinerConfig | None = None) -> str:
    if config is not None and config.sc2_executable_path:
        return config.sc2_executable_path
    executable = find_sc2_executable()
    return str(executable) if executable is not None else ""


def _resolve_sc2_executable(sc2_path: object = "") -> Path | None:
    path_text = str(sc2_path or "").strip().strip('"')
    if not path_text:
        return find_sc2_executable()

    executable = Path(path_text)
    if executable.is_file():
        return executable

    logger.warning("Configured SC2_x64.exe path was not found; path=%s", executable)
    return None


def scan_rooms(
    scan_seconds: float,
    discovery_port: int,
    sc2_path: object = "",
) -> tuple[Any, str, str, str, str, dict[str, Any]]:
    duration = _safe_float(scan_seconds, DEFAULT_SCAN_SECONDS)
    port = int(discovery_port or DEFAULT_DISCOVERY_PORT)
    logger.info("GUI scan started; port=%s duration_sec=%s", port, duration)
    client = LanDiscoveryClient(port=port)
    diagnostics = LanScanDiagnostics()
    try:
        rooms = client.scan(duration_sec=duration, diagnostics=diagnostics, debug_logger=logger, log_payloads=True)
    except OSError as exc:
        diagnostics.log_summary(logger, "GUI scan")
        logger.exception("GUI scan failed; port=%s duration_sec=%s", port, duration)
        sc2_executable = _resolve_sc2_executable(sc2_path)
        status = f"Scan LAN failed on UDP port {port}. Manual host fallback is still available."
        return (
            _dropdown_update([], None),
            _render_scan_error(port, exc, diagnostics),
            _render_no_room_preview(sc2_executable),
            status,
            "",
            {"rooms": [], "labels": [], "manual_room": None},
        )
    diagnostics.log_summary(logger, "GUI scan")
    labels = [_room_label(index, room) for index, room in enumerate(rooms)]
    new_state = {"rooms": rooms, "labels": labels, "manual_room": None}
    logger.info("GUI scan completed; rooms=%s", len(rooms))

    if not rooms:
        sc2_executable = _resolve_sc2_executable(sc2_path)
        logger.info("GUI scan found no rooms; sc2_executable=%s", sc2_executable or "not found")
        status = (
            f"No LAV StarCraft II rooms found on UDP port {port}. "
            "UDP broadcast may be blocked; use Check Manual Proxy with the default Manual host."
        )
        return (
            _dropdown_update([], None),
            _render_no_rooms_scan_result(port, diagnostics),
            _render_no_room_preview(sc2_executable),
            status,
            "",
            new_state,
        )

    selected = labels[0]
    room = rooms[0]
    sc2_executable = _resolve_sc2_executable(sc2_path)
    logger.info(
        "GUI selected first room; room_id=%s source_id=%s host=%s ports=%s sc2_executable=%s",
        room.room_id,
        room.source_id,
        room.proxy_host or room.sender_ip or room.host_name,
        ",".join(str(port) for port in room.proxy_ports),
        sc2_executable or "not found",
    )
    status = f"Found {len(rooms)} LAV StarCraft II room(s)."
    return (
        _dropdown_update(labels, selected),
        _render_room_details(room, sc2_executable, "Found LAV StarCraft II room"),
        _render_launch_preview(room, sc2_executable),
        status,
        "",
        new_state,
    )


def select_room(
    selected_label: str | None,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, str, str]:
    room = _selected_room(selected_label, state)
    if room is None:
        logger.info("GUI room selection cleared or invalid; selected_label=%s", selected_label)
        return "", _render_no_room_preview(_resolve_sc2_executable(sc2_path)), "No room selected.", ""

    sc2_executable = _resolve_sc2_executable(sc2_path)
    logger.info("GUI room selected; room_id=%s source_id=%s", room.room_id, room.source_id)
    return (
        _render_room_details(room, sc2_executable, "Selected Scan LAN room"),
        _render_launch_preview(room, sc2_executable),
        "Room selected.",
        "",
    )


def check_selected_proxy(selected_label: str | None, state: dict[str, Any]) -> str:
    room = _selected_room(selected_label, state)
    if room is None:
        logger.info("GUI proxy check skipped; no room selected")
        return "No Scan LAN room selected."

    logger.info("GUI proxy check started; room_id=%s host=%s ports=%s", room.room_id, select_room_connect_host(room), room.proxy_ports)
    checks = check_proxy_ports(room)
    for check in checks:
        logger.info(
            "GUI proxy check result; host=%s port=%s reachable=%s error=%s",
            check.host,
            check.port,
            check.reachable,
            check.error,
        )
    return _render_port_checks(checks)


def prepare_selected_sc2(
    selected_label: str | None,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str]:
    room = _selected_room(selected_label, state) or (state or {}).get("manual_room")
    if room is None:
        logger.info("GUI SC2 prepare skipped; no room selected")
        return "No Scan LAN room selected.", ""

    sc2_executable = _resolve_sc2_executable(sc2_path)
    if sc2_executable is None:
        logger.warning("GUI SC2 prepare skipped; SC2_x64.exe not found")
        return "SC2_x64.exe was not found. Set a valid SC2_x64.exe path in Settings.", ""

    logger.info(
        "GUI SC2 prepare requested; room_id=%s source_id=%s sc2_executable=%s timeout_sec=%s",
        room.room_id,
        room.source_id,
        sc2_executable,
        DEFAULT_SC2_API_READY_TIMEOUT_SEC,
    )
    result = _REMOTE_START_SERVER.prepare_sc2(
        room,
        sc2_executable,
        ready_timeout_sec=DEFAULT_SC2_API_READY_TIMEOUT_SEC,
    )
    if result.get("ok"):
        status = "SC2 prepared. 5679 is listening and SC2 API Ping succeeded."
    else:
        status = f"SC2 prepare failed: {result.get('error', 'unknown_error')}"
    return status, _render_sc2_prepare_result(result)


def join_selected_lobby(
    selected_label: str | None,
    player_name: object,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, dict[str, Any]]:
    room = _selected_room(selected_label, state)
    if room is None:
        logger.info("GUI lobby join skipped; no room selected")
        return "No Scan LAN room selected.", "", state or {}

    logger.info(
        "GUI lobby join requested; room_id=%s source_id=%s target=%s:%s",
        room.room_id,
        room.source_id,
        select_room_connect_host(room),
        room.join_port or DEFAULT_JOIN_PORT,
    )
    sc2_executable = _resolve_sc2_executable(sc2_path)
    map_sync = ensure_room_map_file(room, sc2_executable)
    map_status = _render_map_sync_result(map_sync)
    logger.info("GUI map sync result before lobby join: %s", map_sync)
    if not map_sync.ok:
        logger.warning("GUI lobby join blocked by map sync failure; error=%s", map_sync.error)
        return "Map sync failed. Lobby join was not sent.", map_status, state or {}

    if _is_same_joined_room(state, room):
        logger.info("GUI lobby join skipped; room already joined room_id=%s source_id=%s", room.room_id, room.source_id)
        listener_status = _start_remote_start_listener(room, sc2_path)
        status = "Lobby already joined. Host can press Start Game / Ladder Proxy."
        return status, "\n".join([map_status, "", "Lobby join skipped: already joined.", "", listener_status]), state or {}

    result = send_lobby_join(room, player_name=str(player_name or "Human"))
    lobby_status = _render_lobby_join_result(result)
    if not result.ok:
        logger.warning("GUI lobby join failed; error=%s", result.error)
        return "Lobby join failed.", "\n".join([map_status, "", lobby_status]), state or {}

    logger.info("GUI lobby join accepted; room_id=%s client_id=%s", room.room_id, result.client_id)
    listener_status = _start_remote_start_listener(room, sc2_path)
    status = "Lobby join accepted. Host can now press Start Game / Ladder Proxy."
    return status, "\n".join([map_status, "", lobby_status, "", listener_status]), _state_with_joined_room(state, room, result)


def join_selected_room(
    selected_label: str | None,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str]:
    room = _selected_room(selected_label, state)
    if room is None:
        logger.info("GUI join skipped; no room selected")
        return "No Scan LAN room selected.", ""

    sc2_executable = _resolve_sc2_executable(sc2_path)
    if sc2_executable is None:
        logger.warning("GUI join skipped; SC2_x64.exe not found")
        return "SC2_x64.exe was not found. Set a valid SC2_x64.exe path in Settings.", ""

    logger.info("GUI join requested; room_id=%s source_id=%s sc2_executable=%s", room.room_id, room.source_id, sc2_executable)
    plan = build_launch_plan(human_slot_room(room), sc2_executable)
    process = launch_sc2(plan)
    logger.info("GUI join launch process started; pid=%s command=%s", process.pid, plan.command)
    return (
        f"StarCraft II launch requested. PID: {process.pid}",
        _render_remote_human_launch_note(room),
    )


def use_manual_host(
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, str, str, dict[str, Any]]:
    room, error = _manual_room_from_inputs(manual_host, manual_proxy_ports, manual_start_port)
    if room is None:
        logger.info("GUI manual host rejected; error=%s", error)
        return "", _render_no_room_preview(_resolve_sc2_executable(sc2_path)), error, "", _state_with_manual_room(state, None)

    sc2_executable = _resolve_sc2_executable(sc2_path)
    logger.info("GUI manual host selected; host=%s ports=%s", room.proxy_host, room.proxy_ports)
    return (
        _render_room_details(room, sc2_executable, "Manual LAV StarCraft II target"),
        _render_launch_preview(room, sc2_executable),
        "Manual host target ready.",
        "",
        _state_with_manual_room(state, room),
    )


def check_manual_proxy(
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, str, str, dict[str, Any]]:
    room, error = _manual_room_from_inputs(manual_host, manual_proxy_ports, manual_start_port)
    if room is None:
        logger.info("GUI manual proxy check skipped; error=%s", error)
        return "", _render_no_room_preview(_resolve_sc2_executable(sc2_path)), error, "", _state_with_manual_room(state, None)

    sc2_executable = _resolve_sc2_executable(sc2_path)
    logger.info("GUI manual proxy check started; host=%s ports=%s", room.proxy_host, room.proxy_ports)
    checks = check_proxy_ports(room)
    for check in checks:
        logger.info(
            "GUI manual proxy check result; host=%s port=%s reachable=%s error=%s",
            check.host,
            check.port,
            check.reachable,
            check.error,
        )
    proxy_status = _render_port_checks(checks)
    status = (
        "Manual proxy check passed. Scan LAN is not required; Join Manual Host can proceed."
        if checks and all(check.reachable for check in checks)
        else "Manual proxy check failed. Join Manual Host will stay blocked until both proxy ports are reachable."
    )
    return (
        _render_room_details(room, sc2_executable, "Manual LAV StarCraft II target"),
        _render_launch_preview(room, sc2_executable),
        status,
        proxy_status,
        _state_with_manual_room(state, room),
    )


def join_manual_room(
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, str, str, dict[str, Any]]:
    room, error = _manual_room_from_inputs(manual_host, manual_proxy_ports, manual_start_port)
    if room is None:
        logger.info("GUI manual join skipped; error=%s", error)
        return "", _render_no_room_preview(_resolve_sc2_executable(sc2_path)), error, "", _state_with_manual_room(state, None)

    sc2_executable = _resolve_sc2_executable(sc2_path)
    if sc2_executable is None:
        logger.warning("GUI manual join skipped; SC2_x64.exe not found")
        return (
            _render_room_details(room, None, "Manual LAV StarCraft II target"),
            _render_launch_preview(room, None),
            "SC2_x64.exe was not found. Set a valid SC2_x64.exe path in Settings.",
            "",
            _state_with_manual_room(state, room),
        )

    logger.info("GUI manual join requested; host=%s ports=%s sc2_executable=%s", room.proxy_host, room.proxy_ports, sc2_executable)
    plan = build_launch_plan(human_slot_room(room), sc2_executable)
    process = launch_sc2(plan)
    logger.info("GUI manual join launch process started; pid=%s command=%s", process.pid, plan.command)
    return (
        _render_room_details(room, sc2_executable, "Manual LAV StarCraft II target"),
        _render_launch_preview(room, sc2_executable),
        f"StarCraft II launch requested. PID: {process.pid}",
        _render_remote_human_launch_note(room),
        _state_with_manual_room(state, room),
    )


def join_manual_lobby(
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    manual_join_port: object,
    player_name: object,
    state: dict[str, Any],
    sc2_path: object = "",
) -> tuple[str, str, str, str, dict[str, Any]]:
    room, error = _manual_room_from_inputs(
        manual_host,
        manual_proxy_ports,
        manual_start_port,
        manual_join_port,
    )
    if room is None:
        logger.info("GUI manual lobby join skipped; error=%s", error)
        return "", _render_no_room_preview(_resolve_sc2_executable(sc2_path)), error, "", _state_with_manual_room(state, None)

    sc2_executable = _resolve_sc2_executable(sc2_path)
    logger.info(
        "GUI manual lobby join requested; host=%s join_port=%s",
        room.proxy_host,
        room.join_port or DEFAULT_JOIN_PORT,
    )
    result = send_lobby_join(
        room,
        player_name=str(player_name or "Human"),
        target_host=room.proxy_host,
        target_port=room.join_port or DEFAULT_JOIN_PORT,
    )
    lobby_status = _render_lobby_join_result(result)
    listener_status = _start_remote_start_listener(room, sc2_path) if result.ok else ""
    status = "Manual lobby join accepted. Host can now press Start Game / Ladder Proxy." if result.ok else "Manual lobby join failed."
    return (
        _render_room_details(room, sc2_executable, "Manual LAV StarCraft II target"),
        _render_launch_preview(room, sc2_executable),
        status,
        "\n".join([lobby_status, "", listener_status]).rstrip(),
        _state_with_joined_room(_state_with_manual_room(state, room), room, result) if result.ok else _state_with_manual_room(state, room),
    )


def _selected_room(selected_label: str | None, state: dict[str, Any]) -> LanRoom | None:
    if not selected_label or not state:
        return None

    labels = state.get("labels", [])
    rooms = state.get("rooms", [])
    try:
        index = labels.index(selected_label)
    except ValueError:
        return None
    if index < 0 or index >= len(rooms):
        return None
    return rooms[index]


def _manual_room_from_inputs(
    manual_host: object,
    manual_proxy_ports: object,
    manual_start_port: object,
    manual_join_port: object = None,
) -> tuple[LanRoom | None, str]:
    host = str(manual_host or "").strip()
    if not host:
        return None, "Manual host is required."

    try:
        ports = parse_proxy_ports(manual_proxy_ports or DEFAULT_MANUAL_PROXY_PORTS)
    except ValueError as exc:
        return None, f"Invalid manual proxy ports: {exc}"

    try:
        start_port = int(manual_start_port or DEFAULT_MANUAL_START_PORT)
    except (TypeError, ValueError):
        return None, "Manual start port must be an integer."

    if start_port <= 0 or start_port > 65535:
        return None, "Manual start port must be between 1 and 65535."

    try:
        join_port = int(manual_join_port or DEFAULT_MANUAL_JOIN_PORT)
    except (TypeError, ValueError):
        return None, "Manual join port must be an integer."

    if join_port <= 0 or join_port > 65535:
        return None, "Manual join port must be between 1 and 65535."

    return (
        LanRoom(
            protocol=LAV_LAN_ROOM_PROTOCOL,
            version=LAV_LAN_ROOM_VERSION,
            source_id="manual",
            room_id=f"manual-{host}",
            room_name="Manual LAV StarCraft II",
            mode="observer",
            proxy_host=host,
            proxy_ports=ports,
            start_port=start_port,
            join_port=join_port,
            human_client_port=DEFAULT_HUMAN_CLIENT_PORT,
            remote_start_port=DEFAULT_REMOTE_START_PORT,
        ),
        "",
    )


def _start_remote_start_listener(room: LanRoom, sc2_path: object = "") -> str:
    sc2_executable = _resolve_sc2_executable(sc2_path)
    desired_port = room.remote_start_port if room.remote_start_port is not None else DEFAULT_REMOTE_START_PORT
    if _REMOTE_START_SERVER.port != desired_port:
        _REMOTE_START_SERVER.stop()
        _REMOTE_START_SERVER.port = desired_port
    result = _REMOTE_START_SERVER.start(room, sc2_executable)
    logger.info("Remote start listener status: %s", result)
    lines = [
        "Remote start listener",
        f"Listen: {result.get('bind_host', '0.0.0.0')}:{result.get('port', DEFAULT_REMOTE_START_PORT)}",
        f"Room: {result.get('room_id', room.room_id)}",
        f"SC2 executable: {sc2_executable or 'not found'}",
    ]
    if sc2_executable is None:
        lines.append("Set a valid SC2_x64.exe path before the host starts the game.")
    return "\n".join(lines)


def _state_with_manual_room(state: dict[str, Any], room: LanRoom | None) -> dict[str, Any]:
    new_state = dict(state or {})
    new_state.setdefault("rooms", [])
    new_state.setdefault("labels", [])
    new_state["manual_room"] = room
    return new_state


def _state_with_joined_room(
    state: dict[str, Any],
    room: LanRoom,
    result: LobbyJoinResult,
) -> dict[str, Any]:
    new_state = dict(state or {})
    new_state.setdefault("rooms", [])
    new_state.setdefault("labels", [])
    new_state["joined_room"] = room
    new_state["lobby_join"] = result
    return new_state


def _is_same_joined_room(state: dict[str, Any], room: LanRoom) -> bool:
    if not isinstance(state, dict):
        return False
    joined = state.get("joined_room")
    if not isinstance(joined, LanRoom):
        return False
    return joined.room_id == room.room_id and joined.source_id == room.source_id


def _render_scan_error(port: int, error: OSError, diagnostics: LanScanDiagnostics) -> str:
    return "\n".join(
        [
            "Scan LAN failed",
            f"UDP discovery port: {port}",
            f"Error: {error}",
            *_render_scan_diagnostic_lines(diagnostics),
            *_manual_fallback_lines(),
        ]
    )


def _render_no_rooms_scan_result(port: int, diagnostics: LanScanDiagnostics) -> str:
    return "\n".join(
        [
            "No LAV StarCraft II rooms found",
            f"UDP discovery port: {port}",
            *_render_scan_diagnostic_lines(diagnostics),
            *_manual_fallback_lines(),
        ]
    )


def _render_scan_diagnostic_lines(diagnostics: LanScanDiagnostics) -> list[str]:
    reasons = _format_counts(diagnostics.parse_failure_reasons)
    senders = _format_counts(diagnostics.sender_counts)
    return [
        f"UDP bind: {'OK' if diagnostics.bind_succeeded else 'FAILED'}",
        f"Bind host: {diagnostics.bind_host or '0.0.0.0'}",
        f"Bind port: {diagnostics.port}",
        f"Scan duration seconds: {diagnostics.duration_sec}",
        f"Scan started: {diagnostics.started_at}",
        f"Scan ended: {diagnostics.ended_at}",
        f"UDP packets received: {diagnostics.packets_received}",
        f"Payload parse successes: {diagnostics.parsed_rooms}",
        f"Payload parse failures: {diagnostics.parse_failures}",
        f"Parse failure reasons: {reasons}",
        f"Sender IPs: {senders}",
        f"Final rooms count: {diagnostics.rooms_returned}",
        *_render_packet_event_lines(diagnostics),
    ]


def _manual_fallback_lines() -> list[str]:
    return [
        "",
        "Manual Host fallback",
        f"Host: {DEFAULT_MANUAL_HOST}",
        f"Proxy ports: {DEFAULT_MANUAL_PROXY_PORTS}",
        "UDP broadcast may be blocked; use Check Manual Proxy.",
        "If Check Manual Proxy is OK, use Join Manual Host even when Scan LAN finds 0 rooms.",
    ]


def _render_packet_event_lines(diagnostics: LanScanDiagnostics) -> list[str]:
    if not diagnostics.packet_events:
        return ["UDP packet samples: none"]

    lines = ["UDP packet samples:"]
    for index, event in enumerate(diagnostics.packet_events, start=1):
        lines.append(
            f"  #{index} sender={event.sender_ip}:{event.sender_port} "
            f"bytes={event.byte_count} parse={event.parse_result}"
        )
        lines.append(f"     raw={event.raw_payload}")
    return lines


def _format_counts(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _render_room_details(room: LanRoom, sc2_executable: object, title: str) -> str:
    host = select_room_connect_host(room) or room.proxy_host or room.sender_ip or room.host_name
    return "\n".join(
        [
            title,
            f"Room: {room.room_name}",
            f"Host: {host}",
            f"Bot: {room.preferred_bot}",
            f"Map: {room.preferred_map}",
            f"Map file: {room.map_file_name or 'not advertised'}",
            f"Map download port: {room.map_download_port or DEFAULT_MAP_DOWNLOAD_PORT if room.map_file_name else 'not advertised'}",
            f"Proxy ports: {','.join(str(port) for port in room.proxy_ports)}",
            f"Start port: {room.start_port if room.start_port is not None else ''}",
            f"Join port: {room.join_port if room.join_port is not None else DEFAULT_JOIN_PORT}",
            f"Human SC2 API port: {room.human_client_port if room.human_client_port is not None else DEFAULT_HUMAN_CLIENT_PORT}",
            f"Remote start port: {room.remote_start_port if room.remote_start_port is not None else DEFAULT_REMOTE_START_PORT}",
            f"Multiplayer relay: {bool(room.multiplayer_relay_enabled)}",
            f"Multiplayer relay ports: {','.join(str(port) for port in room.multiplayer_relay_ports) or 'derived from start port'}",
            f"SC2 executable: {sc2_executable or 'not found'}",
        ]
    )


def _render_lobby_join_result(result: LobbyJoinResult) -> str:
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


def _render_map_sync_result(result: MapSyncResult) -> str:
    lines = [
        "Map sync result",
        f"OK: {result.ok}",
    ]
    if result.skipped:
        lines.append(f"Skipped: {result.skipped}")
    if result.action:
        lines.append(f"Action: {result.action}")
    if result.destination:
        lines.append(f"Destination: {result.destination}")
    if result.url:
        lines.append(f"URL: {result.url}")
    if result.error:
        lines.append(f"Error: {result.error}")
        if "map_write_permission_denied" in result.error:
            lines.append("Run HumanJoiner as administrator or copy the map into the SC2 Maps folder manually.")
    return "\n".join(lines)


def _render_launch_preview(room: LanRoom, sc2_executable: object) -> str:
    probe_host = select_room_connect_host(room)
    lines = [
        "SC2 launch command:",
        f"  {build_command_preview(sc2_executable, human_slot_room(room) if sc2_executable is not None else None)}",
        "Connection environment:",
    ]
    for name, value in build_environment_preview(human_slot_room(room)).items():
        lines.append(f"  {name}={value}")
    lines.append("TCP probe targets before join:")
    if probe_host and room.proxy_ports:
        for port in room.proxy_ports:
            lines.append(f"  {probe_host}:{port}")
    else:
        lines.append("  <proxy host or ports missing>")
    lines.append("Remote start listener after Join Lobby:")
    lines.append(f"  0.0.0.0:{room.remote_start_port if room.remote_start_port is not None else DEFAULT_REMOTE_START_PORT}")
    return "\n".join(lines)


def _render_no_room_preview(sc2_executable: object) -> str:
    return "\n".join(
        [
            "SC2 launch command:",
            f"  {build_command_preview(sc2_executable)}",
            "Connection environment:",
            "  <scan LAN or enter a manual host first>",
            "TCP probe targets before join:",
            f"  {DEFAULT_MANUAL_HOST}:5677",
            f"  {DEFAULT_MANUAL_HOST}:5678",
            "Remote start listener after Join Lobby:",
            f"  0.0.0.0:{DEFAULT_REMOTE_START_PORT}",
        ]
    )


def _render_port_checks(checks: list[PortCheck]) -> str:
    lines: list[str] = []
    for check in checks:
        if check.reachable:
            lines.append(f"OK {check.host}:{check.port}")
        else:
            target = f"{check.host}:{check.port}" if check.port else check.host
            lines.append(f"FAILED {target} {check.error}".rstrip())
    return "\n".join(lines)


def _render_sc2_prepare_result(result: dict[str, Any]) -> str:
    lines = [
        "SC2 prepare / warm-up result",
        f"OK: {bool(result.get('ok'))}",
    ]
    if result.get("message"):
        lines.append(f"Message: {result.get('message')}")
    if result.get("error"):
        lines.append(f"Error: {result.get('error')}")
    if result.get("pid") is not None:
        lines.append(f"PID: {result.get('pid')}")
    if result.get("human_client_port") is not None:
        lines.append(f"Human client port: {result.get('human_client_port')}")
    if "port_ready" in result:
        lines.append(f"Port ready: {bool(result.get('port_ready'))}")
    if "api_ready" in result:
        lines.append(f"API ready: {bool(result.get('api_ready'))}")
    if result.get("api_ready_attempts") is not None:
        lines.append(f"API ready attempts: {result.get('api_ready_attempts')}")
    if result.get("api_ready_error"):
        lines.append(f"API ready error: {result.get('api_ready_error')}")
    relay = result.get("multiplayer_relay")
    if isinstance(relay, dict):
        relay_config = relay.get("config") if isinstance(relay.get("config"), dict) else {}
        relay_ports = relay.get("selected_ports") or relay_config.get("ports", []) or []
        lines.append(
            "Multiplayer relay: "
            f"ok={bool(relay.get('ok'))} "
            f"running={bool(relay.get('running'))} "
            f"bind={relay.get('selected_bind_host') or relay_config.get('bind_host') or ''} "
            f"ports={','.join(str(port) for port in relay_ports)}"
        )
        if relay.get("error"):
            lines.append(f"Multiplayer relay error: {relay.get('error')}")
    loopback_relay = result.get("loopback_relay")
    if isinstance(loopback_relay, dict):
        loopback_config = (
            loopback_relay.get("config")
            if isinstance(loopback_relay.get("config"), dict)
            else {}
        )
        loopback_ports = (
            loopback_relay.get("selected_ports")
            or loopback_config.get("ports", [])
            or []
        )
        lines.append(
            "Loopback relay: "
            f"ok={bool(loopback_relay.get('ok'))} "
            f"running={bool(loopback_relay.get('running'))} "
            f"bind={loopback_relay.get('selected_bind_host') or loopback_config.get('bind_host') or ''} "
            f"target={loopback_relay.get('selected_peer_host') or loopback_config.get('target_host') or ''} "
            f"ports={','.join(str(port) for port in loopback_ports)}"
        )
        if loopback_relay.get("error"):
            lines.append(f"Loopback relay error: {loopback_relay.get('error')}")
    if result.get("ok"):
        lines.append("Host can press Start Game / Ladder Proxy after Join Lobby is accepted.")
    return "\n".join(lines)


def _render_remote_human_launch_note(room: LanRoom) -> str:
    port = room.human_client_port if room.human_client_port is not None else DEFAULT_HUMAN_CLIENT_PORT
    return "\n".join(
        [
            "Remote human SC2 API listener",
            f"Local listen: 0.0.0.0:{port}",
            "Host proxy ports are diagnostic only for this mode.",
        ]
    )


def _room_label(index: int, room: LanRoom) -> str:
    host = select_room_connect_host(room) or room.proxy_host or room.sender_ip or room.host_name or "unknown"
    bot = room.preferred_bot or "unknown bot"
    map_name = room.preferred_map or "unknown map"
    room_name = room.room_name or "LAV StarCraft II"
    return f"{index + 1}. {room_name} | {host} | {bot} | {map_name} | {_short_id(room.room_id)}"


def _short_id(value: str) -> str:
    if len(value) <= 12:
        return value
    return value[:12]


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0 or parsed > 65535:
        return default
    return parsed


def _dropdown_update(choices: list[str], value: str | None) -> Any:
    try:
        import gradio as gr
    except ImportError:
        return {"choices": choices, "value": value}
    return gr.update(choices=choices, value=value)


if __name__ == "__main__":
    raise SystemExit(main())
