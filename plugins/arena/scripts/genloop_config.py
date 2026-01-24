#!/usr/bin/env python3
"""
Genloop Configuration Loading

Load and validate genloop configuration files for constraint-driven generation.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from models import Constraint

logger = logging.getLogger("arena")

# Default values
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_AGENTS = ["claude", "codex", "gemini"]


@dataclasses.dataclass
class RoutingRule:
    """Pattern-based routing rule."""
    match: str  # Glob pattern for constraint IDs
    agents: List[str]


@dataclasses.dataclass
class PriorityRouting:
    """Priority-based routing rule."""
    range: Tuple[int, int]  # (min, max) priority values
    agents: List[str]


@dataclasses.dataclass
class ConstraintRouting:
    """Constraint routing configuration."""
    default_agents: List[str] = dataclasses.field(default_factory=lambda: DEFAULT_AGENTS.copy())
    rules: List[RoutingRule] = dataclasses.field(default_factory=list)
    priority_routing: List[PriorityRouting] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConstraintRouting":
        rules = []
        for rule_data in d.get("rules", []):
            rules.append(RoutingRule(
                match=rule_data.get("match", "*"),
                agents=rule_data.get("agents", DEFAULT_AGENTS.copy()),
            ))

        priority_routing = []
        for pr_data in d.get("priority_routing", []):
            range_val = pr_data.get("range", [1, 10])
            priority_routing.append(PriorityRouting(
                range=(range_val[0], range_val[1]),
                agents=pr_data.get("agents", DEFAULT_AGENTS.copy()),
            ))

        return cls(
            default_agents=d.get("default_agents", DEFAULT_AGENTS.copy()),
            rules=rules,
            priority_routing=priority_routing,
        )


@dataclasses.dataclass
class ConstraintConfig:
    """Constraint configuration."""
    dir: Optional[str] = None  # Directory containing constraint YAML files
    files: List[str] = dataclasses.field(default_factory=list)  # Specific constraint files
    routing: ConstraintRouting = dataclasses.field(default_factory=ConstraintRouting)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConstraintConfig":
        return cls(
            dir=d.get("dir"),
            files=d.get("files", []),
            routing=ConstraintRouting.from_dict(d.get("routing", {})),
        )


@dataclasses.dataclass
class GeneratePhaseConfig:
    """Generate phase configuration."""
    agent: str = "claude"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GeneratePhaseConfig":
        return cls(agent=d.get("agent", "claude"))


@dataclasses.dataclass
class CritiquePhaseConfig:
    """Critique phase configuration."""
    pattern: str = "parallel"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CritiquePhaseConfig":
        return cls(pattern=d.get("pattern", "parallel"))


@dataclasses.dataclass
class AdjudicatePhaseConfig:
    """Adjudicate phase configuration."""
    agent: str = "claude"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AdjudicatePhaseConfig":
        return cls(agent=d.get("agent", "claude"))


@dataclasses.dataclass
class RefinePhaseConfig:
    """Refine phase configuration."""
    agent: str = "claude"
    mode: str = "edit"  # edit | rewrite
    validation_retries: int = 2
    max_size_change_pct: float = 20.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RefinePhaseConfig":
        return cls(
            agent=d.get("agent", "claude"),
            mode=d.get("mode", "edit"),
            validation_retries=d.get("validation_retries", 2),
            max_size_change_pct=d.get("max_size_change_pct", 20.0),
        )


@dataclasses.dataclass
class PhasesConfig:
    """Phases configuration."""
    generate: GeneratePhaseConfig = dataclasses.field(default_factory=GeneratePhaseConfig)
    critique: CritiquePhaseConfig = dataclasses.field(default_factory=CritiquePhaseConfig)
    adjudicate: AdjudicatePhaseConfig = dataclasses.field(default_factory=AdjudicatePhaseConfig)
    refine: RefinePhaseConfig = dataclasses.field(default_factory=RefinePhaseConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PhasesConfig":
        return cls(
            generate=GeneratePhaseConfig.from_dict(d.get("generate", {})),
            critique=CritiquePhaseConfig.from_dict(d.get("critique", {})),
            adjudicate=AdjudicatePhaseConfig.from_dict(d.get("adjudicate", {})),
            refine=RefinePhaseConfig.from_dict(d.get("refine", {})),
        )


@dataclasses.dataclass
class TensionAxis:
    """A tension axis for balancing constraints."""
    axis: str
    guidance: Optional[str] = None
    winner_on_conflict: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TensionAxis":
        return cls(
            axis=d.get("axis", ""),
            guidance=d.get("guidance"),
            winner_on_conflict=d.get("winner_on_conflict"),
        )


@dataclasses.dataclass
class ApprovalConfig:
    """Approval configuration."""
    block_on: List[str] = dataclasses.field(default_factory=lambda: ["CRITICAL", "HIGH"])
    policy: Optional[str] = None  # no_critical | no_critical_or_high | all_resolved

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApprovalConfig":
        return cls(
            block_on=d.get("block_on", ["CRITICAL", "HIGH"]),
            policy=d.get("policy"),
        )


@dataclasses.dataclass
class EscalationConfig:
    """Escalation configuration."""
    triggers: List[str] = dataclasses.field(
        default_factory=lambda: ["max_iterations", "thrashing", "conflicting_criticals"]
    )
    thrash_threshold: int = 2

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EscalationConfig":
        return cls(
            triggers=d.get("triggers", ["max_iterations", "thrashing", "conflicting_criticals"]),
            thrash_threshold=d.get("thrash_threshold", 2),
        )


@dataclasses.dataclass
class AdjudicationConfig:
    """Adjudication configuration."""
    approval: ApprovalConfig = dataclasses.field(default_factory=ApprovalConfig)
    escalation: EscalationConfig = dataclasses.field(default_factory=EscalationConfig)
    tensions: List[TensionAxis] = dataclasses.field(default_factory=list)
    instructions: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AdjudicationConfig":
        tensions = []
        for t in d.get("tensions", []):
            tensions.append(TensionAxis.from_dict(t))

        return cls(
            approval=ApprovalConfig.from_dict(d.get("approval", {})),
            escalation=EscalationConfig.from_dict(d.get("escalation", {})),
            tensions=tensions,
            instructions=d.get("instructions"),
        )


@dataclasses.dataclass
class TerminationConfig:
    """Termination policy configuration."""
    approve_when: str = "no_critical_and_no_high"
    escalate_on: List[str] = dataclasses.field(
        default_factory=lambda: ["max_iterations", "thrashing"]
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TerminationConfig":
        return cls(
            approve_when=d.get("approve_when", "no_critical_and_no_high"),
            escalate_on=d.get("escalate_on", ["max_iterations", "thrashing"]),
        )


@dataclasses.dataclass
class OutputConfig:
    """Output configuration."""
    dir: str = "final"
    filename: str = "artifact.md"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OutputConfig":
        return cls(
            dir=d.get("dir", "final"),
            filename=d.get("filename", "artifact.md"),
        )


@dataclasses.dataclass
class GenloopConfig:
    """Complete genloop configuration."""
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    allow_scripts: bool = False
    constraints: ConstraintConfig = dataclasses.field(default_factory=ConstraintConfig)
    phases: PhasesConfig = dataclasses.field(default_factory=PhasesConfig)
    adjudication: AdjudicationConfig = dataclasses.field(default_factory=AdjudicationConfig)
    termination: TerminationConfig = dataclasses.field(default_factory=TerminationConfig)
    output: OutputConfig = dataclasses.field(default_factory=OutputConfig)
    source_path: Optional[Path] = None  # Path to the config file

    @classmethod
    def from_dict(cls, d: Dict[str, Any], source_path: Optional[Path] = None) -> "GenloopConfig":
        return cls(
            max_iterations=d.get("max_iterations", DEFAULT_MAX_ITERATIONS),
            allow_scripts=d.get("allow_scripts", False),
            constraints=ConstraintConfig.from_dict(d.get("constraints", {})),
            phases=PhasesConfig.from_dict(d.get("phases", {})),
            adjudication=AdjudicationConfig.from_dict(d.get("adjudication", {})),
            termination=TerminationConfig.from_dict(d.get("termination", {})),
            output=OutputConfig.from_dict(d.get("output", {})),
            source_path=source_path,
        )


def load_genloop_config(config_path: Path) -> GenloopConfig:
    """Load and validate genloop configuration from a YAML file.

    Args:
        config_path: Path to the genloop config YAML file

    Returns:
        GenloopConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file has invalid YAML
        ValueError: If config file has invalid structure
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Genloop config not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Invalid YAML in genloop config: {e}")

    if not isinstance(raw, dict):
        raise ValueError(f"Genloop config must be a YAML dict, got: {type(raw).__name__}")

    # TODO: Add JSON schema validation when schema file is created
    # validate_schema(raw, "genloop-config.schema.json")

    config = GenloopConfig.from_dict(raw, source_path=config_path)
    logger.info(f"Loaded genloop config from {config_path}")
    logger.debug(f"  max_iterations: {config.max_iterations}")
    logger.debug(f"  allow_scripts: {config.allow_scripts}")
    logger.debug(f"  constraints.dir: {config.constraints.dir}")
    logger.debug(f"  constraints.routing.default_agents: {config.constraints.routing.default_agents}")

    return config


def get_agents_for_constraint(
    constraint: Constraint,
    config: Optional[GenloopConfig] = None,
    available_agents: Optional[List[str]] = None,
) -> List[str]:
    """Determine which agents should critique this constraint.

    Resolution order (highest to lowest priority):
    1. Per-constraint `agents` field in constraint YAML
    2. Config file `constraints.routing.rules` pattern match
    3. Config file `constraints.routing.priority_routing` range match
    4. Config file `constraints.routing.default_agents`
    5. Built-in default: ["claude", "codex", "gemini"]

    Args:
        constraint: The constraint to get agents for
        config: Optional GenloopConfig with routing rules
        available_agents: List of agents that are actually configured (filters result)

    Returns:
        List of agent names that should critique this constraint
    """
    # 1. Per-constraint override (highest priority)
    if hasattr(constraint, 'agents') and constraint.agents:
        agents = constraint.agents
        logger.debug(f"Constraint {constraint.id}: using per-constraint agents: {agents}")
    elif config is None:
        # No config, use default
        agents = DEFAULT_AGENTS.copy()
        logger.debug(f"Constraint {constraint.id}: no config, using default agents: {agents}")
    else:
        routing = config.constraints.routing
        agents = None

        # 2. Pattern-based rules
        for rule in routing.rules:
            if fnmatch.fnmatch(constraint.id, rule.match):
                agents = rule.agents
                logger.debug(
                    f"Constraint {constraint.id}: matched rule '{rule.match}' -> {agents}"
                )
                break

        # 3. Priority-based routing
        if agents is None:
            for pr in routing.priority_routing:
                min_pri, max_pri = pr.range
                if min_pri <= constraint.priority <= max_pri:
                    agents = pr.agents
                    logger.debug(
                        f"Constraint {constraint.id}: matched priority range "
                        f"[{min_pri}, {max_pri}] -> {agents}"
                    )
                    break

        # 4. Default from config
        if agents is None:
            agents = routing.default_agents
            logger.debug(f"Constraint {constraint.id}: using config default agents: {agents}")

    # Filter by available agents if specified
    if available_agents:
        filtered = [a for a in agents if a in available_agents]
        if filtered != agents:
            removed = set(agents) - set(filtered)
            logger.warning(
                f"Constraint {constraint.id}: removed unavailable agents {removed}"
            )
        agents = filtered

    return agents


def resolve_constraint_paths(
    config: GenloopConfig,
    project_root: Path,
) -> List[Path]:
    """Resolve constraint file paths from config.

    Args:
        config: Genloop configuration
        project_root: Project root directory for relative path resolution

    Returns:
        List of resolved constraint file paths
    """
    paths: List[Path] = []

    # From specific files
    for file_path in config.constraints.files:
        resolved = Path(file_path)
        if not resolved.is_absolute():
            resolved = project_root / resolved
        if resolved.exists():
            paths.append(resolved)
        else:
            logger.warning(f"Constraint file not found: {resolved}")

    # From directory
    if config.constraints.dir:
        dir_path = Path(config.constraints.dir)
        if not dir_path.is_absolute():
            dir_path = project_root / dir_path
        if dir_path.exists() and dir_path.is_dir():
            for yaml_file in sorted(dir_path.glob("*.yaml")):
                if yaml_file not in paths:
                    paths.append(yaml_file)
        else:
            logger.warning(f"Constraint directory not found: {dir_path}")

    return paths
