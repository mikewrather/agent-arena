#!/usr/bin/env python3
"""
Agent Arena Response Parsers

Functions for parsing agent outputs: envelopes, critiques, adjudications.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Tuple

import yaml

from models import (
    Envelope,
    Critique, CritiqueIssue,
    Adjudication, AdjudicationDecision,
)

logger = logging.getLogger("arena")


def parse_critique(raw: str, agent_name: str, constraint_id: str, iteration: int) -> Critique:
    """Parse critique JSON from agent output."""
    raw = raw.strip()

    # Try to extract JSON from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1)

    try:
        obj = json.loads(raw)
        critique = Critique.from_dict(obj)
        critique.reviewer = agent_name
        critique.constraint_id = constraint_id
        critique.iteration = iteration
        return critique
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse critique JSON from {agent_name}: {e}")
        # Return an empty critique on parse failure
        return Critique(
            constraint_id=constraint_id,
            reviewer=agent_name,
            iteration=iteration,
            overall="ERROR",
            issues=[],
            approved_sections=[],
            summary=f"Failed to parse critique: {e}",
        )


def parse_adjudication(raw: str, iteration: int) -> Adjudication:
    """Parse adjudication YAML/JSON from agent output."""
    raw = raw.strip()

    # Extract content from markdown code blocks
    # Try ```json first (most common for Claude)
    json_block = re.search(r"```json\s*([\s\S]*?)```", raw, re.DOTALL)
    if json_block:
        raw = json_block.group(1).strip()
    else:
        # Try ```yaml
        yaml_block = re.search(r"```yaml\s*([\s\S]*?)```", raw, re.DOTALL)
        if yaml_block:
            raw = yaml_block.group(1).strip()
        else:
            # Try bare ``` block
            bare_block = re.search(r"```\s*([\s\S]*?)```", raw, re.DOTALL)
            if bare_block:
                raw = bare_block.group(1).strip()

    try:
        # Try JSON first
        obj = json.loads(raw)
    except json.JSONDecodeError:
        try:
            # Fall back to YAML
            obj = yaml.safe_load(raw)
        except Exception as e:
            logger.warning(f"Failed to parse adjudication: {e}")
            return Adjudication(
                iteration=iteration,
                status="ERROR",
                tension_analysis=[],
                decisions=[],
                bill_of_work=f"Failed to parse adjudication: {e}",
            )

    adjudication = Adjudication.from_dict(obj)
    adjudication.iteration = iteration
    return adjudication


def parse_envelope(raw: str, agent_kind: str) -> Tuple[Envelope, str]:
    """Parse agent output into Envelope. Returns (envelope, error_reason)."""
    raw = raw.strip()

    # Handle Gemini's wrapper format: {"response": "...", ...}
    if agent_kind == "gemini":
        try:
            outer = json.loads(raw)
            if isinstance(outer, dict) and "response" in outer:
                inner_raw = outer["response"]
                if isinstance(inner_raw, str):
                    try:
                        inner = json.loads(inner_raw)
                        if isinstance(inner, dict):
                            return Envelope.from_dict(inner), ""
                    except json.JSONDecodeError:
                        pass
                    # Treat response as plain message
                    return Envelope(status="ok", message=inner_raw), ""
                elif isinstance(inner_raw, dict):
                    return Envelope.from_dict(inner_raw), ""
        except json.JSONDecodeError as e:
            return Envelope.error(f"Gemini JSON parse failed: {e}"), str(e)

    # Standard JSON envelope parsing (Claude, Codex)
    # Try to extract JSON from potential markdown code blocks
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1)

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return Envelope.from_dict(obj), ""
        return Envelope.error("Output is not a JSON object"), "not_object"
    except json.JSONDecodeError as e:
        # Truncate raw output for error message
        truncated = raw[:500] + "..." if len(raw) > 500 else raw
        return Envelope.error(f"JSON parse error: {e}. Raw: {truncated}"), str(e)


def validate_artifacts(env: Envelope, base_dir: Path) -> List[str]:
    """Validate artifact paths exist and are within base_dir. Returns list of warnings."""
    warnings = []
    base_resolved = base_dir.resolve()

    for art in env.artifacts:
        art_path = Path(art.get("path", ""))
        if not art_path.is_absolute():
            art_path = base_dir / art_path

        try:
            resolved = art_path.resolve()
            # Security: check path traversal
            if not str(resolved).startswith(str(base_resolved)):
                warnings.append(f"Artifact path escapes base directory: {art.get('path')}")
                logger.warning(f"Path traversal attempt blocked: {art.get('path')}")
                continue
            if not resolved.exists():
                warnings.append(f"Artifact not found: {art.get('path')}")
        except (OSError, ValueError) as e:
            warnings.append(f"Invalid artifact path: {art.get('path')} ({e})")

    return warnings
