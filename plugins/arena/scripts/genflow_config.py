#!/usr/bin/env python3
"""
Genflow Configuration Loading

Configuration module for the Genflow workflow engine - a flexible, configurable
workflow system with per-constraint behaviors and multi-phase support.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml

from models import Constraint
from genloop_config import (
    ConstraintConfig,
    AdjudicationConfig,
    OutputConfig,
    DEFAULT_MAX_ITERATIONS,
)

logger = logging.getLogger("arena")


class IssueBehavior(Enum):
    """Actions per severity level for issue handling."""
    HALT = "halt"           # Stop critique phase, proceed to adjudicate
    CONTINUE = "continue"   # Accumulate issue, keep running
    ESCALATE = "escalate"   # Skip adjudication, go directly to HITL
    IGNORE = "ignore"       # Log but exclude from adjudication


@dataclasses.dataclass
class ConstraintBehavior:
    """Per-severity behavior configuration.

    Defines what action to take when issues of each severity are found.
    """
    critical: IssueBehavior = IssueBehavior.HALT
    high: IssueBehavior = IssueBehavior.HALT
    medium: IssueBehavior = IssueBehavior.CONTINUE
    low: IssueBehavior = IssueBehavior.IGNORE

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConstraintBehavior":
        """Parse behavior from dict. Keys are severity names (case-insensitive)."""
        def parse_behavior(key: str, default: IssueBehavior) -> IssueBehavior:
            # Check both uppercase and lowercase keys
            val = d.get(key.upper()) or d.get(key.lower())
            if val is None:
                return default
            try:
                return IssueBehavior(val.lower())
            except ValueError:
                logger.warning(f"Invalid behavior '{val}' for {key}, using default")
                return default

        return cls(
            critical=parse_behavior("critical", IssueBehavior.HALT),
            high=parse_behavior("high", IssueBehavior.HALT),
            medium=parse_behavior("medium", IssueBehavior.CONTINUE),
            low=parse_behavior("low", IssueBehavior.IGNORE),
        )

    def get_behavior(self, severity: str) -> IssueBehavior:
        """Get behavior for a given severity level."""
        severity_map = {
            "CRITICAL": self.critical,
            "HIGH": self.high,
            "MEDIUM": self.medium,
            "LOW": self.low,
        }
        return severity_map.get(severity.upper(), IssueBehavior.CONTINUE)


@dataclasses.dataclass
class WorkflowStep:
    """A single step in the workflow pipeline.

    Attributes:
        step: Type of step (generate, critique, adjudicate, refine)
        name: Optional step name for loop_to references
        agent: Agent to use (overrides default)
        model: Model override (e.g., "sonnet", "opus", "o3")
        execution: Execution mode for critique (parallel | serial)
        order: Constraint order for critique (priority | definition)
        constraints: Glob patterns to filter constraints (critique only)
        scope: Adjudication scope (accumulated | previous | all)
        mode: Refine mode (edit | rewrite)
        loop_to: Step name to loop back to (refine only)
    """
    step: Literal["generate", "critique", "adjudicate", "refine"]
    name: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    execution: str = "parallel"  # parallel | serial
    order: str = "priority"      # priority | definition
    constraints: Optional[List[str]] = None
    scope: str = "accumulated"   # accumulated | previous | all
    mode: str = "edit"           # edit | rewrite
    loop_to: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkflowStep":
        """Parse workflow step from dict."""
        step_type = d.get("step", "")
        if step_type not in ("generate", "critique", "adjudicate", "refine"):
            raise ValueError(f"Invalid step type: {step_type}")

        return cls(
            step=step_type,
            name=d.get("name"),
            agent=d.get("agent"),
            model=d.get("model"),
            execution=d.get("execution", "parallel"),
            order=d.get("order", "priority"),
            constraints=d.get("constraints"),
            scope=d.get("scope", "accumulated"),
            mode=d.get("mode", "edit"),
            loop_to=d.get("loop_to"),
        )


@dataclasses.dataclass
class GenflowConfig:
    """Complete genflow configuration.

    Attributes:
        workflow: List of workflow steps to execute
        max_iterations: Maximum iterations before escalation
        default_behavior: Default behavior for severities
        constraints: Constraint configuration (reused from genloop)
        adjudication: Adjudication configuration (reused from genloop)
        output: Output configuration (reused from genloop)
        constraint_behaviors: Per-constraint behavior overrides
        source_path: Path to the config file
    """
    workflow: List[WorkflowStep]
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    default_behavior: ConstraintBehavior = dataclasses.field(
        default_factory=ConstraintBehavior
    )
    constraints: ConstraintConfig = dataclasses.field(
        default_factory=ConstraintConfig
    )
    adjudication: AdjudicationConfig = dataclasses.field(
        default_factory=AdjudicationConfig
    )
    output: OutputConfig = dataclasses.field(default_factory=OutputConfig)
    constraint_behaviors: Dict[str, ConstraintBehavior] = dataclasses.field(
        default_factory=dict
    )
    source_path: Optional[Path] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any], source_path: Optional[Path] = None) -> "GenflowConfig":
        """Parse genflow config from dict."""
        # Parse workflow steps
        workflow = []
        for step_data in d.get("workflow", []):
            workflow.append(WorkflowStep.from_dict(step_data))

        if not workflow:
            raise ValueError("Genflow config must have at least one workflow step")

        # Parse default behavior
        default_behavior = ConstraintBehavior.from_dict(d.get("default_behavior", {}))

        # Parse constraint configurations
        constraints = ConstraintConfig.from_dict(d.get("constraints", {}))
        adjudication = AdjudicationConfig.from_dict(d.get("adjudication", {}))
        output = OutputConfig.from_dict(d.get("output", {}))

        return cls(
            workflow=workflow,
            max_iterations=d.get("max_iterations", DEFAULT_MAX_ITERATIONS),
            default_behavior=default_behavior,
            constraints=constraints,
            adjudication=adjudication,
            output=output,
            constraint_behaviors={},  # Populated from constraint files
            source_path=source_path,
        )


def load_genflow_config(config_path: Path) -> GenflowConfig:
    """Load and validate genflow configuration from a YAML file.

    Args:
        config_path: Path to the genflow config YAML file

    Returns:
        GenflowConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file has invalid YAML
        ValueError: If config file has invalid structure
    """
    config_path = Path(config_path) if not isinstance(config_path, Path) else config_path
    if not config_path.exists():
        raise FileNotFoundError(f"Genflow config not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Invalid YAML in genflow config: {e}")

    if not isinstance(raw, dict):
        raise ValueError(f"Genflow config must be a YAML dict, got: {type(raw).__name__}")

    config = GenflowConfig.from_dict(raw, source_path=config_path)

    logger.info(f"Loaded genflow config from {config_path}")
    logger.debug(f"  max_iterations: {config.max_iterations}")
    logger.debug(f"  workflow steps: {len(config.workflow)}")
    logger.debug(f"  constraints.dir: {config.constraints.dir}")

    return config


def get_behavior_for_severity(
    constraint: Constraint,
    severity: str,
    config: GenflowConfig,
) -> IssueBehavior:
    """Determine behavior for an issue based on constraint and severity.

    Resolution order (highest to lowest priority):
    1. Per-constraint behavior override in constraint YAML
    2. Per-constraint behavior in config.constraint_behaviors
    3. Config default_behavior
    4. Built-in defaults

    Args:
        constraint: The constraint the issue came from
        severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW)
        config: Genflow configuration

    Returns:
        IssueBehavior for this severity on this constraint
    """
    # 1. Check constraint's own behavior field
    if hasattr(constraint, 'behavior') and constraint.behavior:
        behavior = ConstraintBehavior.from_dict(constraint.behavior)
        result = behavior.get_behavior(severity)
        logger.debug(
            f"Constraint {constraint.id}/{severity}: using per-constraint behavior -> {result.value}"
        )
        return result

    # 2. Check config's per-constraint behaviors
    if constraint.id in config.constraint_behaviors:
        behavior = config.constraint_behaviors[constraint.id]
        result = behavior.get_behavior(severity)
        logger.debug(
            f"Constraint {constraint.id}/{severity}: using config constraint behavior -> {result.value}"
        )
        return result

    # 3. Use config default behavior
    result = config.default_behavior.get_behavior(severity)
    logger.debug(
        f"Constraint {constraint.id}/{severity}: using default behavior -> {result.value}"
    )
    return result


def resolve_constraints_for_step(
    step: WorkflowStep,
    all_constraints: List[Constraint],
) -> List[Constraint]:
    """Resolve which constraints apply to a workflow step.

    Args:
        step: The workflow step (must be critique step)
        all_constraints: All loaded constraints

    Returns:
        Filtered list of constraints matching the step's patterns
    """
    if step.step != "critique":
        return []

    # No filter patterns means all constraints
    if not step.constraints:
        selected = all_constraints
    else:
        # Filter by glob patterns
        selected = []
        for constraint in all_constraints:
            for pattern in step.constraints:
                if fnmatch.fnmatch(constraint.id, pattern):
                    selected.append(constraint)
                    break

    # Sort by order preference
    if step.order == "priority":
        selected = sorted(selected, key=lambda c: c.priority)
    # "definition" order preserves the order from constraint loading

    logger.debug(
        f"Step '{step.name or step.step}': resolved {len(selected)} constraints "
        f"from patterns {step.constraints or ['*']}"
    )
    return selected


def get_step_by_name(workflow: List[WorkflowStep], name: str) -> Optional[WorkflowStep]:
    """Find a workflow step by name.

    Args:
        workflow: List of workflow steps
        name: Name to search for

    Returns:
        WorkflowStep with matching name, or None
    """
    for step in workflow:
        if step.name == name:
            return step
    return None


def get_step_index_by_name(workflow: List[WorkflowStep], name: str) -> int:
    """Find the index of a workflow step by name.

    Args:
        workflow: List of workflow steps
        name: Name to search for

    Returns:
        Index of step with matching name, or -1 if not found
    """
    for i, step in enumerate(workflow):
        if step.name == name:
            return i
    return -1


def validate_workflow(config: GenflowConfig) -> List[str]:
    """Validate workflow configuration.

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    step_names = set()

    for i, step in enumerate(config.workflow):
        # Check for duplicate names
        if step.name:
            if step.name in step_names:
                errors.append(f"Step {i}: duplicate name '{step.name}'")
            step_names.add(step.name)

        # Validate loop_to references
        if step.loop_to:
            if step.step != "refine":
                errors.append(
                    f"Step {i}: loop_to is only valid for refine steps"
                )
            if step.loop_to not in step_names:
                # Check if any later step has this name
                found = any(s.name == step.loop_to for s in config.workflow)
                if not found:
                    errors.append(
                        f"Step {i}: loop_to references non-existent step '{step.loop_to}'"
                    )

        # Validate adjudicate scope
        if step.step == "adjudicate" and step.scope not in ("accumulated", "previous", "all"):
            errors.append(
                f"Step {i}: invalid scope '{step.scope}' (use accumulated, previous, or all)"
            )

        # Validate critique execution mode
        if step.step == "critique" and step.execution not in ("parallel", "serial"):
            errors.append(
                f"Step {i}: invalid execution '{step.execution}' (use parallel or serial)"
            )

    # Check workflow has required steps
    step_types = [s.step for s in config.workflow]
    if "generate" not in step_types:
        errors.append("Workflow must have at least one generate step")

    return errors
