from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

IGNORED_NAMES = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache"}


def _iter_files(root: Path, max_files: int) -> Iterable[Path]:
    stack: List[Path] = [root]
    collected = 0
    while stack and collected < max_files:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
        except (FileNotFoundError, PermissionError):
            continue
        for entry in entries:
            if entry.name in IGNORED_NAMES:
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                yield entry
                collected += 1
                if collected >= max_files:
                    break


def build_workspace_snapshot(root: Path | None = None, max_files: int = 20, max_bytes: int = 2000) -> str:
    root = (root or Path.cwd()).resolve()
    files = list(_iter_files(root, max_files))

    lines: List[str] = [f"Workspace root: {root}", "Files observed:"]
    if not files:
        lines.append("- (no files detected)")
    for path in files:
        try:
            size = path.stat().st_size
        except (FileNotFoundError, PermissionError):
            size = -1
        rel = path.relative_to(root)
        size_info = f"{size} bytes" if size >= 0 else "size unavailable"
        lines.append(f"- {rel} ({size_info})")

    lines.append("\nFile excerpts (truncated):")
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if not text:
            continue
        excerpt = text[:max_bytes].rstrip()
        rel = path.relative_to(root)
        lines.append(f"--- {rel} ---")
        lines.append(excerpt)

    return "\n".join(lines).strip()
