from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from human_joiner_config import HumanJoinerConfig, load_config, save_config


TEST_DIR = Path(__file__).resolve().parents[1] / ".test_tmp" / "human_joiner_config_tests"


def test_config_path(name: str) -> Path:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_DIR / f"{name}_{uuid4().hex}.json"


class HumanJoinerConfigTest(unittest.TestCase):
    def test_load_missing_config_returns_defaults(self) -> None:
        config = load_config(test_config_path("missing"))

        self.assertEqual(config.manual_host, "26.189.202.71")
        self.assertEqual(config.manual_proxy_ports, "5677,5678")
        self.assertEqual(config.manual_start_port, 5690)

    def test_save_and_load_config_round_trips_json(self) -> None:
        path = test_config_path("round_trip")
        original = HumanJoinerConfig(
            sc2_executable_path=r"D:\Games\StarCraft II\Versions\Base100000\SC2_x64.exe",
            manual_host="10.0.0.5",
            manual_proxy_ports="6000,6001",
            manual_start_port=7000,
            manual_join_port=7001,
        )

        saved_path = save_config(original, path)
        loaded = load_config(saved_path)

        self.assertEqual(loaded, original)

    def test_load_config_ignores_bad_values(self) -> None:
        path = test_config_path("bad_values")
        path.write_text(
            '{"manual_start_port": -1, "manual_join_port": "not a port"}',
            encoding="utf-8",
        )

        config = load_config(path)

        self.assertEqual(config.manual_start_port, 5690)
        self.assertGreater(config.manual_join_port, 0)


if __name__ == "__main__":
    unittest.main()
