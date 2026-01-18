#!/usr/bin/env python3
"""
Intelligent Expert Router for Arena Orchestration

LLM-native routing using Claude's semantic understanding.
No embeddings - leverages Claude Code's native reasoning.

Design principles:
- Simple YAML expert definitions
- Single Claude call for selection
- Deterministic (temperature=0)
- Fallback to default panel on error
- Persists routing decision for auditability
"""
from __future__ import annotations

# Bootstrap venv: add local .venv site-packages to path if it exists
# This allows the script to find dependencies (pyyaml) without activation
import sys
from pathlib import Path as _Path
_script_dir = _Path(__file__).resolve().parent
# Try script's own dir first (for .arena/ layout), then parent (for plugin scripts/ layout)
for _venv_base in [_script_dir, _script_dir.parent]:
    _venv_site = _venv_base / ".venv" / "lib"
    if _venv_site.exists():
        for _pydir in _venv_site.iterdir():
            if _pydir.name.startswith("python"):
                _sp = _pydir / "site-packages"
                if _sp.exists() and str(_sp) not in sys.path:
                    sys.path.insert(0, str(_sp))
        break

import json
import logging
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("arena.router")

# Default experts if routing fails
DEFAULT_PANEL = ["architect", "code-reviewer", "security-auditor"]


@dataclass
class RoutingResult:
    """Result of expert selection."""
    selected: list[str]
    reasoning: str
    confidence: str  # high, medium, low
    mode: str
    router_model: str = "claude"
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Expert:
    """Expert persona definition."""
    name: str
    focus: str
    catches: list[str] = None
    complements: list[str] = None
    best_for: list[str] = None
    depth: str = "specialist"  # specialist | generalist

    @classmethod
    def from_dict(cls, data: dict) -> "Expert":
        return cls(
            name=data["name"],
            focus=data["focus"],
            catches=data.get("catches", []),
            complements=data.get("complements", []),
            best_for=data.get("best_for", []),
            depth=data.get("depth", "specialist"),
        )


def load_experts(experts_dir: Path) -> list[Expert]:
    """Load expert definitions from YAML files."""
    experts = []
    if not experts_dir.exists():
        logger.error(f"Experts directory not found: {experts_dir}")
        return experts

    yaml_files = list(experts_dir.glob("*.yaml"))
    if not yaml_files:
        logger.error(f"No .yaml files found in experts directory: {experts_dir}")
        return experts

    for yaml_file in yaml_files:
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data:
                    experts.append(Expert.from_dict(data))
                else:
                    logger.error(f"Expert file is empty or invalid: {yaml_file}")
        except Exception as e:
            # Log at ERROR level - malformed expert files are configuration problems
            logger.error(f"Failed to load expert from {yaml_file}: {e}")

    logger.info(f"Loaded {len(experts)} experts from {experts_dir}")
    return experts


def format_expert_descriptions(experts: list[Expert]) -> str:
    """Format experts for the routing prompt with all relevant fields."""
    lines = []
    for e in experts:
        # Include catches (top 5) - critical for routing decisions
        catches = f", catches: {', '.join(e.catches[:5])}" if e.catches else ""
        complements = f", complements: {', '.join(e.complements)}" if e.complements else ""
        best_for = f", best for: {', '.join(e.best_for)}" if e.best_for else ""
        depth_marker = " [generalist]" if e.depth == "generalist" else ""
        lines.append(f"- **{e.name}**{depth_marker}: {e.focus}{catches}{best_for}{complements}")
    return "\n".join(lines)


def call_claude_router(prompt: str) -> Optional[dict]:
    """Call Claude CLI for routing decision."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--max-turns", "1", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            logger.error(f"Claude router call failed: {result.stderr}")
            return None

        # Parse JSON response
        response = json.loads(result.stdout)

        # Extract the actual response text
        text = None

        # Handle array format from Claude CLI (contains events, assistant message, result)
        if isinstance(response, list):
            # Look for the "result" type entry (usually last)
            for entry in reversed(response):
                if isinstance(entry, dict) and entry.get("type") == "result":
                    text = entry.get("result")
                    break
            # Fallback: look for assistant message
            if text is None:
                for entry in response:
                    if isinstance(entry, dict) and entry.get("type") == "assistant":
                        msg = entry.get("message", {})
                        content = msg.get("content", [])
                        if content and isinstance(content, list) and content[0].get("type") == "text":
                            text = content[0].get("text")
                            break
        elif isinstance(response, dict):
            if "result" in response:
                text = response["result"]
            elif "content" in response:
                text = response["content"]

        if text is None:
            text = result.stdout

        # Find JSON in response
        json_match = None
        if isinstance(text, str):
            # Try to find JSON block
            import re
            json_patterns = [
                r'```json\s*(\{.*?\})\s*```',
                r'(\{[^{}]*"selected"[^{}]*\})',
            ]
            for pattern in json_patterns:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    json_match = match.group(1)
                    break

            if json_match:
                return json.loads(json_match)

            # Try parsing the whole text as JSON
            try:
                return json.loads(text)
            except:
                pass

        logger.warning(f"Could not parse router response: {text[:200] if text else 'None'}")
        return None

    except subprocess.TimeoutExpired:
        logger.error("Claude router call timed out")
        return None
    except Exception as e:
        logger.error(f"Claude router call error: {e}")
        return None


def select_experts(
    goal: str,
    context: Optional[str],
    expert_pool: list[Expert],
    mode: str = "collaborative",
    max_experts: Optional[int] = None,
) -> RoutingResult:
    """Use Claude to select all relevant experts for a goal.

    Args:
        goal: The task/goal to route
        context: Optional additional context
        expert_pool: List of expert definitions
        mode: Orchestration mode (collaborative/adversarial/brainstorming)
        max_experts: Optional cap on experts (None = no limit, select all relevant)

    Returns:
        RoutingResult with selected experts and metadata.
        Check result.success - if False, result.error contains the failure reason.
    """
    # Validate inputs - fail loudly if preconditions not met
    if not expert_pool:
        error_msg = "No experts in pool - cannot route"
        logger.error(error_msg)
        return RoutingResult(
            selected=[],
            reasoning="",
            confidence="low",
            mode=mode,
            success=False,
            error=error_msg,
        )

    expert_descriptions = format_expert_descriptions(expert_pool)
    valid_names = {e.name for e in expert_pool}

    mode_guidance = {
        "adversarial": "Select experts who will challenge each other and identify different issues. Prefer diverse, critical perspectives.",
        "collaborative": "Select experts whose skills complement each other for comprehensive coverage. Prefer experts who build on each other.",
        "brainstorming": "Select experts with diverse backgrounds to generate varied ideas. Prefer creative and cross-domain thinkers.",
    }

    # Build selection instruction based on max_experts
    if max_experts is not None:
        selection_instruction = f"Select ALL relevant experts for this goal (minimum 1, maximum {max_experts}). Only omit experts that add no value."
    else:
        selection_instruction = "Select ALL relevant experts for this goal (minimum 1). Only omit experts that genuinely add no value to this task."

    prompt = f"""You are an expert router for a multi-agent review system. Select the best experts for this task.

## Goal
{goal}

## Context
{context or "No additional context provided."}

## Orchestration Mode
{mode}: {mode_guidance.get(mode, mode_guidance["collaborative"])}

## Available Experts
{expert_descriptions}

## Selection Criteria
1. **Match catches to goal**: Each expert lists what issues they catch. Match these to the goal.
2. **Use best_for**: Pick experts whose "best for" scenarios align with the task.
3. **Respect complements**: Experts list who they work well with - use this for team composition.
4. **Depth matters**: [generalist] experts are better for vague/broad goals. Specialists for specific tasks.
5. **Avoid redundancy**: Don't pick experts with overlapping catches unless adversarial mode.

## Mode-Specific Guidance
- **collaborative**: Pick complementary experts who cover different aspects together.
- **adversarial**: Pick experts who will find DIFFERENT issues. Overlap in catches is OK if they'll challenge each other.
- **brainstorming**: Pick diverse backgrounds. Prefer generalists and cross-domain experts.

{selection_instruction}

## Response Format
Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{"selected": ["expert1", "expert2", ...], "reasoning": "One paragraph explaining selection", "confidence": "high|medium|low"}}
"""

    response = call_claude_router(prompt)

    if not response:
        error_msg = "Claude router call returned no response"
        logger.error(error_msg)
        return RoutingResult(
            selected=[],
            reasoning="",
            confidence="low",
            mode=mode,
            success=False,
            error=error_msg,
        )

    if "selected" not in response:
        error_msg = f"Claude router response missing 'selected' field: {response}"
        logger.error(error_msg)
        return RoutingResult(
            selected=[],
            reasoning="",
            confidence="low",
            mode=mode,
            success=False,
            error=error_msg,
        )

    # Validate selections exist in pool - log any invalid names
    raw_selected = response.get("selected", [])
    invalid_names = [s for s in raw_selected if s not in valid_names]
    if invalid_names:
        logger.warning(f"Router returned invalid expert names (not in pool): {invalid_names}")
        logger.warning(f"Valid experts are: {list(valid_names)}")

    selected = [s for s in raw_selected if s in valid_names]

    # If router couldn't select any valid experts, that's a failure
    if not selected:
        error_msg = f"Router selected no valid experts. Raw selection: {raw_selected}, valid pool: {list(valid_names)}"
        logger.error(error_msg)
        return RoutingResult(
            selected=[],
            reasoning=response.get("reasoning", ""),
            confidence="low",
            mode=mode,
            success=False,
            error=error_msg,
        )

    # Apply max_experts cap if specified
    if max_experts is not None and len(selected) > max_experts:
        logger.info(f"Capping selected experts from {len(selected)} to {max_experts}")
        selected = selected[:max_experts]

    logger.info(f"Router selected {len(selected)} experts: {selected}")

    return RoutingResult(
        selected=selected,
        reasoning=response.get("reasoning", ""),
        confidence=response.get("confidence", "medium"),
        mode=mode,
        success=True,
    )


def save_routing_result(result: RoutingResult, run_dir: Path) -> None:
    """Persist routing decision for auditability."""
    routing_file = run_dir / "routing.json"
    with open(routing_file, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    logger.info(f"Saved routing decision to {routing_file}")


def main():
    """CLI entry point for standalone testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Intelligent Expert Router")
    parser.add_argument("--goal", required=True, help="Goal/task to route")
    parser.add_argument("--context", help="Additional context")
    parser.add_argument("--experts-dir", type=Path, help="Directory with expert YAML files")
    parser.add_argument("--mode", default="collaborative", choices=["collaborative", "adversarial", "brainstorming"])
    parser.add_argument("--max-experts", type=int, help="Maximum experts to select (default: no limit)")
    parser.add_argument("--output", type=Path, help="Output file for routing result")

    args = parser.parse_args()

    # Load experts
    experts = []
    if args.experts_dir:
        experts = load_experts(args.experts_dir)

    # Run routing
    result = select_experts(
        goal=args.goal,
        context=args.context,
        expert_pool=experts,
        mode=args.mode,
        max_experts=args.max_experts,
    )

    # Output
    output = result.to_dict()
    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Saved to {args.output}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
