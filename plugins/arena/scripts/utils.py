#!/usr/bin/env python3
"""
Agent Arena Utilities

Common utility functions for file I/O, validation, and path handling.
Extracted from arena.py to enable modular imports.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, IO, Optional

import logging

logger = logging.getLogger("arena")

# Valid characters for mode/persona names (security: prevent path traversal)
VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Global live log file handle (set by orchestrator)
_live_log: Optional[IO[str]] = None


def set_live_log(log_file: Optional[IO[str]]) -> None:
    """Set the global live log file handle."""
    global _live_log
    _live_log = log_file


def get_live_log() -> Optional[IO[str]]:
    """Get the global live log file handle."""
    return _live_log


def write_live(msg: str, prefix: str = "") -> None:
    """Write to live log file for real-time monitoring via tail -f."""
    if _live_log:
        ts = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {prefix}{msg}\n" if prefix else f"[{ts}] {msg}\n"
        _live_log.write(line)
        _live_log.flush()


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_text(path: Path) -> str:
    """Read text from file, return empty string if file doesn't exist."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def ensure_secure_dir(path: Path) -> None:
    """Create directory with 0700 permissions (owner only) for security."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRWXU)  # 0700: rwx for owner only


def write_text_atomic(path: Path, text: str) -> None:
    """Atomic write: write to temp file, fsync, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def append_jsonl_durable(path: Path, obj: Dict[str, Any]) -> None:
    """Append to JSONL with fsync for durability (not atomic, but durable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def load_json(path: Path, default: Any) -> Any:
    """Load JSON from file. Returns default if file missing or invalid."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in {path}: {e}")
        return default


def save_json_atomic(path: Path, obj: Any) -> None:
    """Save object as JSON with atomic write."""
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False))


def normalize_for_hash(s: str) -> str:
    """Normalize string for comparison/hashing."""
    return " ".join(s.strip().lower().split())


def sha256(s: str) -> str:
    """Return truncated SHA256 hash of string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def text_similarity(a: str, b: str) -> float:
    """Simple text similarity using SequenceMatcher (0.0-1.0)."""
    return SequenceMatcher(None, normalize_for_hash(a), normalize_for_hash(b)).ratio()


def validate_name(name: str, kind: str) -> None:
    """Validate mode/persona name to prevent path traversal."""
    if not VALID_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid {kind} name '{name}': must contain only alphanumeric, underscore, or hyphen"
        )


def is_subpath(path: Path, parent: Path) -> bool:
    """Check if path is within parent directory (security check)."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_path_template(
    path_template: str,
    ctx: Dict[str, Path],
    base_dir: Path,
) -> Path:
    """Resolve path template with variable substitution and security check.

    Variables supported:
        {{run_dir}} - The run directory (.arena/runs/<name>/)
        {{project_root}} - Project root (where .arena/ lives)
        {{artifact}} - Path to current artifact file
        {{source}} - Path to source.md (if exists)
        {{constraint_dir}} - Directory containing the constraint file
        {{arena_home}} - Global arena home (~/.arena/)

    Args:
        path_template: Path string with optional {{variables}}
        ctx: Dict mapping variable names to Path values
        base_dir: Base directory for relative path resolution

    Returns:
        Resolved absolute Path

    Raises:
        ValueError: If resolved path escapes allowed directories
    """
    resolved = path_template
    for var, value in ctx.items():
        resolved = resolved.replace(f"{{{{{var}}}}}", str(value))

    path = Path(resolved)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()

    # Security: must be within allowed directories
    allowed = [ctx.get("run_dir"), ctx.get("project_root"), ctx.get("arena_home")]
    allowed = [a for a in allowed if a is not None]

    if not any(is_subpath(path, root) for root in allowed):
        raise ValueError(f"Path escapes allowed directories: {path}")

    return path
