"""Crash-safe JSON persistence and content hashing.

Extracted here because every package needs it (engine, extraction, analysis, service,
assistant). The Windows retry loops are not incidental: a polling reader or antivirus can
briefly hold a file open, and a naive write would either raise or truncate a file another
thread is reading.
"""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never truncate the visible JSON in place: write a sibling temp file and replace it.
    content = json.dumps(value, allow_nan=False, separators=(",", ":"))
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        for attempt in range(80):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 79:
                    raise
                time.sleep(.025)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path):
    """Read a JSON file that another thread may be replacing (Windows denies reads mid-replace)."""
    path = Path(path)
    for attempt in range(40):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.025)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
