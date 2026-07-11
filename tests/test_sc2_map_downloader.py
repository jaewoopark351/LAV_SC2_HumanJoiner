from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sc2_lan_discovery_client import DEFAULT_MAP_DOWNLOAD_PORT, LanRoom
from sc2_map_downloader import ensure_room_map_file, sc2_maps_dir


def sample_room(**overrides: object) -> LanRoom:
    data = {
        "protocol": "lav.sc2.lan_room",
        "version": 1,
        "source_id": "source-1",
        "room_id": "room-1",
        "room_name": "LAV StarCraft II",
        "preferred_map": "PersephoneLE.SC2Map",
        "proxy_host": "192.168.0.67",
        "proxy_ports": [5677, 5678],
        "map_file_name": "PersephoneLE.SC2Map",
        "map_download_port": DEFAULT_MAP_DOWNLOAD_PORT,
        "map_download_path": "/map/PersephoneLE.SC2Map",
    }
    data.update(overrides)
    return LanRoom(**data)


class Sc2MapDownloaderTest(unittest.TestCase):
    def test_sc2_maps_dir_infers_starcraft_root_from_base_executable(self) -> None:
        executable = Path(r"C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe")

        self.assertEqual(
            sc2_maps_dir(executable),
            Path(r"C:\Program Files (x86)\StarCraft II\Maps"),
        )

    def test_existing_matching_map_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "StarCraft II"
            executable = root / "Versions" / "Base97425" / "SC2_x64.exe"
            maps_dir = root / "Maps"
            maps_dir.mkdir(parents=True)
            data = b"map-bytes"
            (maps_dir / "PersephoneLE.SC2Map").write_bytes(data)
            room = sample_room(map_size=len(data), map_sha256=hashlib.sha256(data).hexdigest())

            result = ensure_room_map_file(room, executable)

        self.assertTrue(result.ok)
        self.assertEqual("already_present", result.action)

    def test_missing_map_is_downloaded_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "StarCraft II"
            executable = root / "Versions" / "Base97425" / "SC2_x64.exe"
            data = b"downloaded-map"
            room = sample_room(
                map_size=len(data),
                map_sha256=hashlib.sha256(data).hexdigest(),
            )
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = data

            with patch("sc2_map_downloader.urlopen", return_value=response) as urlopen:
                result = ensure_room_map_file(room, executable)

            destination = root / "Maps" / "PersephoneLE.SC2Map"
            self.assertTrue(result.ok)
            self.assertEqual("downloaded", result.action)
            self.assertEqual(data, destination.read_bytes())
            urlopen.assert_called_once()
            self.assertIn(":47627/map/PersephoneLE.SC2Map", result.url)


if __name__ == "__main__":
    unittest.main()
