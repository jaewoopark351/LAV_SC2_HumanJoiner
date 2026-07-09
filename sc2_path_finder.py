from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


SC2_EXECUTABLE_NAME = "SC2_x64.exe"

DEFAULT_SC2_EXECUTABLE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\StarCraft II\Versions\Base97425\SC2_x64.exe"),
    Path(r"C:\Program Files\StarCraft II\Versions\Base97425\SC2_x64.exe"),
)


def find_sc2_executable(
    *,
    candidate_paths: Iterable[Path | str] | None = None,
    search_roots: Iterable[Path | str] | None = None,
) -> Path | None:
    candidates = DEFAULT_SC2_EXECUTABLE_CANDIDATES if candidate_paths is None else candidate_paths
    roots = default_program_file_roots() if search_roots is None else search_roots

    for candidate in _unique_paths(candidates):
        if candidate.is_file():
            return candidate

    for root in _unique_paths(roots):
        found = _find_latest_base_executable(root)
        if found is not None:
            return found

    return None


def default_program_file_roots() -> tuple[Path, ...]:
    roots = (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    )
    return tuple(_unique_paths(roots))


def _find_latest_base_executable(program_files_root: Path) -> Path | None:
    versions_dir = program_files_root / "StarCraft II" / "Versions"
    try:
        if not versions_dir.is_dir():
            return None
        base_dirs = [path for path in versions_dir.iterdir() if path.is_dir() and path.name.startswith("Base")]
    except OSError:
        return None

    for base_dir in sorted(base_dirs, key=_base_version_sort_key, reverse=True):
        executable = base_dir / SC2_EXECUTABLE_NAME
        if executable.is_file():
            return executable

    return None


def _base_version_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"Base(\d+)", path.name)
    if match is None:
        return (-1, path.name)
    return (int(match.group(1)), path.name)


def _unique_paths(paths: Iterable[Path | str]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        normalized = str(Path(path))
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(Path(path))
    return result
