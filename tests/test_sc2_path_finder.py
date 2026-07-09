from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from sc2_path_finder import SC2_EXECUTABLE_NAME, find_sc2_executable


class FindSc2ExecutableTest(unittest.TestCase):
    def test_returns_existing_direct_candidate(self) -> None:
        candidate = Path(r"C:\Custom\StarCraft II\Versions\Base97425") / SC2_EXECUTABLE_NAME

        with patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path == candidate):
            found = find_sc2_executable(candidate_paths=[candidate], search_roots=[])

        self.assertEqual(found, candidate)

    def test_fallback_finds_latest_base_directory(self) -> None:
        root = Path(r"C:\Program Files")
        versions = root / "StarCraft II" / "Versions"
        older = versions / "Base97425"
        newer = versions / "Base100000"
        executable_paths = {older / SC2_EXECUTABLE_NAME, newer / SC2_EXECUTABLE_NAME}
        directory_paths = {versions, older, newer}

        with (
            patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path in executable_paths),
            patch.object(Path, "is_dir", autospec=True, side_effect=lambda path: path in directory_paths),
            patch.object(Path, "iterdir", autospec=True, side_effect=lambda path: iter([older, newer])),
        ):
            found = find_sc2_executable(candidate_paths=[], search_roots=[root])

        self.assertEqual(found, newer / SC2_EXECUTABLE_NAME)

    def test_returns_none_when_not_found(self) -> None:
        with (
            patch.object(Path, "is_file", autospec=True, return_value=False),
            patch.object(Path, "is_dir", autospec=True, return_value=False),
        ):
            found = find_sc2_executable(candidate_paths=[], search_roots=[Path(r"C:\Missing")])

        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
