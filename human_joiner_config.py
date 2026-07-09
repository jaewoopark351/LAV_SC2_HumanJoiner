from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sc2_lan_discovery_client import DEFAULT_JOIN_PORT


CONFIG_PATH = Path(__file__).resolve().parent / "config" / "human_joiner_config.json"
DEFAULT_MANUAL_HOST = "26.189.202.71"
DEFAULT_MANUAL_PROXY_PORTS = "5677,5678"
DEFAULT_MANUAL_START_PORT = 5690
DEFAULT_MANUAL_JOIN_PORT = DEFAULT_JOIN_PORT


@dataclass(frozen=True)
class HumanJoinerConfig:
    sc2_executable_path: str = ""
    manual_host: str = DEFAULT_MANUAL_HOST
    manual_proxy_ports: str = DEFAULT_MANUAL_PROXY_PORTS
    manual_start_port: int = DEFAULT_MANUAL_START_PORT
    manual_join_port: int = DEFAULT_MANUAL_JOIN_PORT


def load_config(path: Path | str = CONFIG_PATH) -> HumanJoinerConfig:
    config_path = Path(path)
    if not config_path.is_file():
        return HumanJoinerConfig()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HumanJoinerConfig()

    if not isinstance(data, dict):
        return HumanJoinerConfig()

    return config_from_dict(data)


def save_config(config: HumanJoinerConfig, path: Path | str = CONFIG_PATH) -> Path:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config_to_dict(config), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config_path


def config_from_dict(data: dict[str, Any]) -> HumanJoinerConfig:
    return HumanJoinerConfig(
        sc2_executable_path=_string_value(data.get("sc2_executable_path"), ""),
        manual_host=_string_value(data.get("manual_host"), DEFAULT_MANUAL_HOST),
        manual_proxy_ports=_string_value(data.get("manual_proxy_ports"), DEFAULT_MANUAL_PROXY_PORTS),
        manual_start_port=_int_value(data.get("manual_start_port"), DEFAULT_MANUAL_START_PORT),
        manual_join_port=_int_value(data.get("manual_join_port"), DEFAULT_MANUAL_JOIN_PORT),
    )


def config_to_dict(config: HumanJoinerConfig) -> dict[str, object]:
    return {
        "sc2_executable_path": config.sc2_executable_path,
        "manual_host": config.manual_host,
        "manual_proxy_ports": config.manual_proxy_ports,
        "manual_start_port": config.manual_start_port,
        "manual_join_port": config.manual_join_port,
    }


def _string_value(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _int_value(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0 or parsed > 65535:
        return default
    return parsed
