#!/usr/bin/env python3
"""
Agent Arena Source Resolution

Handles source block resolution with path variables, file reading, glob expansion,
and script execution for reliable generation constraints.
"""
from __future__ import annotations

import dataclasses
import glob as glob_module
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import is_subpath, read_text, resolve_path_template

logger = logging.getLogger("arena")

# Limits for source resolution
MAX_FILE_SIZE = 1_000_000  # 1MB per file
MAX_TOTAL_SIZE_WARNING = 100_000  # 100KB warning threshold
MAX_GLOB_FILES = 100  # Maximum files from glob expansion
SCRIPT_TIMEOUT = 30  # Seconds


@dataclasses.dataclass
class SourceBlock:
    """Source block definition from YAML."""
    files: List[str] = dataclasses.field(default_factory=list)
    globs: List[str] = dataclasses.field(default_factory=list)
    scripts: List[str] = dataclasses.field(default_factory=list)
    inline: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["SourceBlock"]:
        """Create SourceBlock from YAML dict. Returns None if data is None/empty."""
        if not data:
            return None
        return cls(
            files=data.get("files", []) or [],
            globs=data.get("globs", []) or [],
            scripts=data.get("scripts", []) or [],
            inline=data.get("inline", "") or "",
        )

    def is_empty(self) -> bool:
        """Check if source block has no content."""
        return not (self.files or self.globs or self.scripts or self.inline)


@dataclasses.dataclass
class ResolvedSources:
    """Result of resolving a source block."""
    content: str
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def files_read(self) -> List[str]:
        return self.metadata.get("files_read", [])

    @property
    def errors(self) -> List[str]:
        return self.metadata.get("errors", [])

    @property
    def warnings(self) -> List[str]:
        return self.metadata.get("warnings", [])


def read_files_with_headers(
    paths: List[Path],
    max_file_size: int = MAX_FILE_SIZE,
) -> Tuple[str, List[str], List[str]]:
    """Read files with per-file size limit and headers.

    Args:
        paths: List of file paths to read
        max_file_size: Maximum size per file in bytes

    Returns:
        (content, files_read, errors)
    """
    content_parts: List[str] = []
    files_read: List[str] = []
    errors: List[str] = []

    for path in paths:
        try:
            if not path.exists():
                errors.append(f"File not found: {path}")
                continue

            if not path.is_file():
                errors.append(f"Not a file: {path}")
                continue

            size = path.stat().st_size
            if size > max_file_size:
                errors.append(f"File too large ({size} bytes, max {max_file_size}): {path}")
                continue

            text = path.read_text(encoding="utf-8", errors="replace")
            content_parts.append(f"### FILE: {path}\n\n```\n{text}\n```\n")
            files_read.append(str(path))

        except PermissionError:
            errors.append(f"Permission denied: {path}")
        except Exception as e:
            errors.append(f"Error reading {path}: {e}")

    return "\n".join(content_parts), files_read, errors


def expand_globs(
    patterns: List[str],
    base_dir: Path,
    ctx: Dict[str, Path],
    max_files: int = MAX_GLOB_FILES,
) -> Tuple[List[Path], List[str], List[str]]:
    """Expand glob patterns with file count limit.

    Args:
        patterns: List of glob patterns (may contain {{variables}})
        base_dir: Base directory for relative patterns
        ctx: Context for variable resolution
        max_files: Maximum number of files to return

    Returns:
        (paths, warnings, errors)
    """
    all_paths: List[Path] = []
    warnings: List[str] = []
    errors: List[str] = []

    for pattern in patterns:
        try:
            # Resolve variables in pattern
            resolved_pattern = pattern
            for var, value in ctx.items():
                resolved_pattern = resolved_pattern.replace(f"{{{{{var}}}}}", str(value))

            # Make absolute if relative
            if not Path(resolved_pattern).is_absolute():
                resolved_pattern = str(base_dir / resolved_pattern)

            # Expand glob
            matches = sorted(glob_module.glob(resolved_pattern, recursive=True))
            paths = [Path(m) for m in matches if Path(m).is_file()]

            if not paths:
                warnings.append(f"Glob pattern matched no files: {pattern}")
                continue

            all_paths.extend(paths)

        except Exception as e:
            errors.append(f"Glob error for pattern '{pattern}': {e}")

    # Deduplicate and limit
    unique_paths = list(dict.fromkeys(all_paths))
    if len(unique_paths) > max_files:
        warnings.append(f"Glob results truncated: {len(unique_paths)} files, max {max_files}")
        unique_paths = unique_paths[:max_files]

    return unique_paths, warnings, errors


def run_script(
    command: str,
    cwd: Path,
    timeout: int = SCRIPT_TIMEOUT,
) -> Tuple[str, Optional[str]]:
    """Run script and capture stdout.

    Args:
        command: Shell command to run
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        (stdout, error) - error is None on success
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,  # Close stdin to prevent hanging
        )

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")

        if result.returncode != 0:
            return stdout, f"Script exited with code {result.returncode}: {stderr[:200]}"

        return stdout, None

    except subprocess.TimeoutExpired:
        return "", f"Script timed out after {timeout}s"
    except Exception as e:
        return "", f"Script execution error: {e}"


def resolve_source_block(
    source_block: SourceBlock,
    ctx: Dict[str, Path],
    base_dir: Path,
    allow_scripts: bool = False,
) -> ResolvedSources:
    """Resolve a source block to content string.

    Args:
        source_block: The source block to resolve
        ctx: Context with path variables (run_dir, project_root, arena_home, etc.)
        base_dir: Base directory for relative paths
        allow_scripts: Whether to execute scripts (requires --allow-scripts flag)

    Returns:
        ResolvedSources with content and metadata
    """
    content_parts: List[str] = []
    files_read: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    # Allowed directories for file reads
    allowed_read_dirs = [
        ctx.get("project_root"),
        ctx.get("run_dir"),
        ctx.get("arena_home"),  # Read-only for shared resources
    ]
    allowed_read_dirs = [d for d in allowed_read_dirs if d is not None]

    # Allowed directories for script execution (more restrictive)
    allowed_script_dirs = [
        ctx.get("project_root"),
        ctx.get("run_dir"),
    ]
    allowed_script_dirs = [d for d in allowed_script_dirs if d is not None]

    # 1. Process files
    if source_block.files:
        resolved_paths: List[Path] = []
        for file_template in source_block.files:
            try:
                resolved_path = resolve_path_template(file_template, ctx, base_dir)

                # Security: verify path is within allowed directories
                if not any(is_subpath(resolved_path, d) for d in allowed_read_dirs):
                    errors.append(f"File outside allowed directories: {file_template}")
                    continue

                resolved_paths.append(resolved_path)
            except ValueError as e:
                errors.append(f"Invalid file path '{file_template}': {e}")

        if resolved_paths:
            file_content, read_files, read_errors = read_files_with_headers(resolved_paths)
            content_parts.append(file_content)
            files_read.extend(read_files)
            errors.extend(read_errors)

    # 2. Process globs
    if source_block.globs:
        glob_paths, glob_warnings, glob_errors = expand_globs(
            source_block.globs, base_dir, ctx
        )
        warnings.extend(glob_warnings)
        errors.extend(glob_errors)

        # Filter to allowed directories
        valid_paths: List[Path] = []
        for path in glob_paths:
            if any(is_subpath(path, d) for d in allowed_read_dirs):
                valid_paths.append(path)
            else:
                errors.append(f"Glob result outside allowed directories: {path}")

        if valid_paths:
            glob_content, glob_files, glob_read_errors = read_files_with_headers(valid_paths)
            content_parts.append(glob_content)
            files_read.extend(glob_files)
            errors.extend(glob_read_errors)

    # 3. Process scripts
    if source_block.scripts:
        if not allow_scripts:
            warnings.append(
                f"Scripts in source block skipped ({len(source_block.scripts)} scripts). "
                "Use --allow-scripts flag to enable."
            )
        else:
            for script_cmd in source_block.scripts:
                # Resolve variables in script command
                resolved_cmd = script_cmd
                for var, value in ctx.items():
                    resolved_cmd = resolved_cmd.replace(f"{{{{{var}}}}}", str(value))

                # Use project_root as working directory
                cwd = ctx.get("project_root", base_dir)

                logger.debug(f"Running script: {resolved_cmd}")
                stdout, error = run_script(resolved_cmd, cwd)

                if error:
                    errors.append(f"Script '{script_cmd[:50]}...': {error}")
                else:
                    content_parts.append(f"### SCRIPT: {script_cmd}\n\n```\n{stdout}\n```\n")

    # 4. Process inline
    if source_block.inline:
        content_parts.append(f"### INLINE SOURCE\n\n{source_block.inline}\n")

    # Combine content
    content = "\n".join(content_parts)

    # Warn if content is large
    if len(content) > MAX_TOTAL_SIZE_WARNING:
        warnings.append(
            f"Large source content: {len(content)} chars "
            f"(warning threshold: {MAX_TOTAL_SIZE_WARNING})"
        )

    return ResolvedSources(
        content=content,
        metadata={
            "files_read": files_read,
            "errors": errors,
            "warnings": warnings,
            "total_size": len(content),
        },
    )


def resolve_legacy_sources(
    sources: List[str],
    ctx: Dict[str, Path],
    base_dir: Path,
) -> Tuple[List[Path], List[str]]:
    """Resolve legacy 'sources' field (paths only, not content).

    This maintains backward compatibility with the old format where
    sources were just path references passed to critics.

    Args:
        sources: List of path templates
        ctx: Context with path variables
        base_dir: Base directory for relative paths

    Returns:
        (resolved_paths, errors)
    """
    resolved_paths: List[Path] = []
    errors: List[str] = []

    logger.warning(
        "Using deprecated 'sources' field. "
        "Migrate to 'source' block for full content resolution."
    )

    for source_template in sources:
        try:
            resolved = resolve_path_template(source_template, ctx, base_dir)
            if resolved.exists():
                resolved_paths.append(resolved)
            else:
                errors.append(f"Source file not found: {resolved}")
        except ValueError as e:
            errors.append(f"Invalid source path '{source_template}': {e}")

    return resolved_paths, errors
