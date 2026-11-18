from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from graphtrace.models import FileRecord, Language

IGNORE_DIRS = {
    ".git",
    ".graphtrace",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    "coverage",
}

EXT_TO_LANG: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
}


def is_test_file(rel_path: str) -> bool:
    p = Path(rel_path)
    name = p.name.lower()
    parts = {part.lower() for part in p.parts}
    if "tests" in parts or "__tests__" in parts or "test" in parts:
        return True
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def scan_repository(repo_root: Path) -> list[tuple[Path, FileRecord]]:
    """Walk repository and produce FileRecords with SHA-256 content hashes."""
    repo_root = repo_root.resolve()
    discovered: list[tuple[Path, FileRecord]] = []
    now = time.time()

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            lang = EXT_TO_LANG.get(ext)
            if not lang:
                continue

            abs_path = Path(dirpath) / fname
            try:
                raw_bytes = abs_path.read_bytes()
            except OSError:
                continue

            rel_path = abs_path.relative_to(repo_root).as_posix()
            sha256 = hashlib.sha256(raw_bytes).hexdigest()
            loc = raw_bytes.count(b"\n") + (1 if raw_bytes else 0)

            record = FileRecord(
                rel_path=rel_path,
                language=lang,
                sha256=sha256,
                loc=loc,
                is_test=is_test_file(rel_path),
                last_indexed_at=now,
            )
            discovered.append((abs_path, record))

    return discovered