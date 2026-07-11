#20260628_kpopmodder: Added HumanJoiner-side SC2 map sync before sending a LAN lobby join.
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from sc2_lan_discovery_client import DEFAULT_MAP_DOWNLOAD_PORT, LanRoom, select_room_connect_host


@dataclass(frozen=True)
class MapSyncResult:
    ok: bool
    skipped: str = ""
    action: str = ""
    destination: str = ""
    url: str = ""
    error: str = ""


def ensure_room_map_file(
    room: LanRoom,
    sc2_executable: Path | None,
    *,
    timeout_sec: float = 20.0,
) -> MapSyncResult:
    if not room.map_file_name:
        return MapSyncResult(ok=True, skipped="no_map_metadata")
    if sc2_executable is None:
        return MapSyncResult(ok=False, error="sc2_executable_missing")

    destination = sc2_maps_dir(sc2_executable) / room.map_file_name
    expected_hash = str(room.map_sha256 or "").strip().lower()
    if destination.is_file() and _matches_expected(destination, room.map_size, expected_hash):
        return MapSyncResult(ok=True, action="already_present", destination=str(destination))

    host = select_room_connect_host(room)
    if not host:
        return MapSyncResult(ok=False, destination=str(destination), error="map_download_host_missing")
    port = room.map_download_port or DEFAULT_MAP_DOWNLOAD_PORT
    path = room.map_download_path or f"/map/{quote(room.map_file_name)}"
    if not path.startswith("/"):
        path = "/" + path
    url = f"http://{host}:{port}{path}"

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(url, timeout=max(0.1, float(timeout_sec or 20.0))) as response:
            data = response.read()
        if room.map_size is not None and int(room.map_size or 0) > 0 and len(data) != int(room.map_size):
            return MapSyncResult(
                ok=False,
                action="downloaded",
                destination=str(destination),
                url=url,
                error=f"map_size_mismatch: expected={room.map_size} actual={len(data)}",
            )
        if expected_hash and hashlib.sha256(data).hexdigest().lower() != expected_hash:
            return MapSyncResult(
                ok=False,
                action="downloaded",
                destination=str(destination),
                url=url,
                error="map_sha256_mismatch",
            )
        destination.write_bytes(data)
    except PermissionError as exc:
        return MapSyncResult(
            ok=False,
            destination=str(destination),
            url=url,
            error=f"map_write_permission_denied: {exc}",
        )
    except OSError as exc:
        return MapSyncResult(
            ok=False,
            destination=str(destination),
            url=url,
            error=f"map_download_failed: {exc}",
        )

    return MapSyncResult(ok=True, action="downloaded", destination=str(destination), url=url)


def sc2_maps_dir(sc2_executable: Path) -> Path:
    root = _infer_sc2_root_from_executable(Path(sc2_executable))
    if root is None:
        return Path(sc2_executable).parent / "Maps"
    return root / "Maps"


def _infer_sc2_root_from_executable(sc2_executable: Path) -> Path | None:
    base_dir = sc2_executable.parent
    if not base_dir.name.lower().startswith("base"):
        return None
    versions_dir = base_dir.parent
    if versions_dir.name.lower() != "versions":
        return None
    return versions_dir.parent


def _matches_expected(path: Path, expected_size: int | None, expected_hash: str) -> bool:
    if expected_size is not None and int(expected_size or 0) > 0 and path.stat().st_size != int(expected_size):
        return False
    if expected_hash and _sha256(path).lower() != expected_hash:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
