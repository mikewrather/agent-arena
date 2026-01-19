#!/usr/bin/env python3
"""
Agent Arena Configuration Loading

Functions for loading modes, personas, profiles, and constraints.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from utils import read_text, load_json, validate_name, write_text_atomic
from models import Constraint

logger = logging.getLogger("arena")


def load_constraints(constraints_dir: Path) -> List[Constraint]:
    """Load all constraint YAML files from a directory, sorted by priority."""
    constraints = []
    if not constraints_dir.exists():
        return constraints

    for yaml_file in constraints_dir.glob("*.yaml"):
        try:
            constraint = Constraint.from_yaml(yaml_file)
            constraints.append(constraint)
            logger.debug(f"Loaded constraint: {constraint.id} (priority {constraint.priority})")
        except Exception as e:
            logger.warning(f"Failed to load constraint {yaml_file}: {e}")

    # Sort by priority (lower = higher priority)
    constraints.sort(key=lambda c: c.priority)
    return constraints


def compress_constraints(constraints: List[Constraint]) -> str:
    """Generate compressed constraint summary for generator agent."""
    if not constraints:
        return ""

    lines = ["# Constraints Summary", ""]
    for constraint in constraints:
        lines.append(f"## {constraint.id.upper()} (Priority {constraint.priority})")
        lines.append("")
        lines.append(constraint.summary.strip())
        lines.append("")

    return "\n".join(lines)


def save_compressed_constraints(run_dir: Path, compressed: str) -> Path:
    """Save compressed constraints to cache directory."""
    cache_dir = run_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "constraints-compressed.md"
    write_text_atomic(cache_path, compressed)
    return cache_path


def load_frontmatter_doc(path: Path) -> Tuple[Dict[str, Any], str]:
    """Load document with YAML frontmatter. Returns (metadata, body)."""
    if not path.exists():
        return {}, ""

    content = read_text(path)
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        return frontmatter, body
    except Exception as e:
        logger.warning(f"YAML parse error in {path}: {e}")
        return {}, content


def load_mode(
    state_dir: Path, mode_name: str, global_dir: Optional[Path] = None
) -> Tuple[Dict[str, Any], str]:
    """Load mode document (YAML frontmatter + body). Checks state_dir first, then global_dir."""
    validate_name(mode_name, "mode")
    # Check local first
    mode_path = state_dir / "modes" / f"{mode_name}.md"
    if mode_path.exists():
        return load_frontmatter_doc(mode_path)
    # Fall back to global
    if global_dir:
        global_path = global_dir / "modes" / f"{mode_name}.md"
        if global_path.exists():
            return load_frontmatter_doc(global_path)
    return {}, ""


def load_persona(
    state_dir: Path, persona_name: str, global_dir: Optional[Path] = None
) -> Tuple[Dict[str, Any], str]:
    """Load persona document (YAML frontmatter + body). Checks state_dir first, then global_dir."""
    validate_name(persona_name, "persona")
    # Check local first
    persona_path = state_dir / "personas" / f"{persona_name}.md"
    if persona_path.exists():
        return load_frontmatter_doc(persona_path)
    # Fall back to global
    if global_dir:
        global_path = global_dir / "personas" / f"{persona_name}.md"
        if global_path.exists():
            return load_frontmatter_doc(global_path)
    return {}, ""


def load_profile(
    state_dir: Path, profile_name: str, global_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Load profile JSON. Checks state_dir first, then global_dir."""
    validate_name(profile_name, "profile")
    # Check local first
    profile_path = state_dir / "profiles" / f"{profile_name}.json"
    if profile_path.exists():
        return load_json(profile_path, {})
    # Fall back to global
    if global_dir:
        global_path = global_dir / "profiles" / f"{profile_name}.json"
        if global_path.exists():
            return load_json(global_path, {})
    logger.warning(f"Profile '{profile_name}' not found")
    return {}


def merge_profile(cfg: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Merge profile settings into config. Profile values override config."""
    merged = cfg.copy()

    # Simple overrides
    simple_keys = [
        "mode", "default_pattern", "order",
        # Routing and multi-expert config
        "routing", "expert_assignment", "expert_agent", "max_experts",
        # Research config
        "enable_research", "research_agent",
        # Multi-phase config
        "phases",
        # Termination config
        "termination",
    ]
    for key in simple_keys:
        if key in profile:
            merged[key] = profile[key]

    # Deep merge agents
    if "agents" in profile:
        merged_agents = merged.get("agents", {}).copy()
        merged_agents.update(profile["agents"])
        merged["agents"] = merged_agents

    # Deep merge personas
    if "personas" in profile:
        merged_personas = merged.get("personas", {}).copy()
        merged_personas.update(profile["personas"])
        merged["personas"] = merged_personas

    return merged
