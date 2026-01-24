#!/usr/bin/env python3
"""
Agent Arena Data Models

Data classes for agent orchestration: locks, agents, envelopes,
constraints, critiques, and adjudications.
"""
from __future__ import annotations

import dataclasses
import fcntl
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, IO

import yaml

from utils import utc_now_iso
from sources import SourceBlock

logger = logging.getLogger("arena")

# Default timeout for agent processes
DEFAULT_TIMEOUT_SECONDS: Optional[int] = None  # No timeout by default


class OrchestratorLock:
    """File-based lock to prevent concurrent orchestrator runs."""

    def __init__(self, state_dir: Path):
        self.lock_path = state_dir / "orchestrator.lock"
        self.lock_file: Optional[IO[str]] = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = open(self.lock_path, "w")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(f"{os.getpid()}\n{utc_now_iso()}\n")
            self.lock_file.flush()
            return True
        except (IOError, OSError):
            self.lock_file.close()
            self.lock_file = None
            return False

    def release(self) -> None:
        if self.lock_file:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None
            if self.lock_path.exists():
                self.lock_path.unlink()


@dataclasses.dataclass
class Agent:
    """Configuration for an agent CLI."""
    name: str
    kind: str  # claude, codex, gemini
    cmd: List[str]
    timeout: Optional[int] = DEFAULT_TIMEOUT_SECONDS
    suppress_stderr: bool = False  # Don't stream stderr to live log


@dataclasses.dataclass
class Envelope:
    """Structured response from an agent."""
    status: str  # ok, needs_human, needs_research, done, error
    message: str
    questions: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    artifacts: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    confidence: Optional[float] = None
    objections: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    agrees_with: List[str] = dataclasses.field(default_factory=list)
    research_topics: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "status": self.status,
            "message": self.message,
            "questions": self.questions,
            "artifacts": self.artifacts,
        }
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.objections:
            d["objections"] = self.objections
        if self.agrees_with:
            d["agrees_with"] = self.agrees_with
        if self.research_topics:
            d["research_topics"] = self.research_topics
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Envelope":
        return cls(
            status=d.get("status", "error"),
            message=d.get("message", ""),
            questions=d.get("questions", []),
            artifacts=d.get("artifacts", []),
            confidence=d.get("confidence"),
            objections=d.get("objections", []),
            agrees_with=d.get("agrees_with", []),
            research_topics=d.get("research_topics", []),
        )

    @classmethod
    def error(cls, msg: str) -> "Envelope":
        return cls(status="error", message=msg)


# =============================================================================
# Reliable Generation: Constraint System
# =============================================================================

@dataclasses.dataclass
class ConstraintRule:
    """A single rule within a constraint."""
    id: str
    text: str
    default_severity: str = "HIGH"
    examples: Optional[Dict[str, str]] = None


@dataclasses.dataclass
class Constraint:
    """A constraint file with rules for critics and summary for generator."""
    id: str
    priority: int
    summary: str
    rules: List[ConstraintRule]
    source_path: Optional[Path] = None
    script: Optional[str] = None  # Optional script to run before critique
    sources: Optional[List[str]] = None  # DEPRECATED: Reference files for critic (paths only)
    source_block: Optional[SourceBlock] = None  # NEW: Full source block with content resolution
    agents: Optional[List[str]] = None  # Per-constraint agent override (e.g., ["claude", "codex"])

    @classmethod
    def from_yaml(cls, path: Path) -> "Constraint":
        """Load constraint from YAML file."""
        content = yaml.safe_load(path.read_text(encoding="utf-8"))

        # Validate: cannot have both 'source' and 'sources'
        has_source = "source" in content and content["source"]
        has_sources = "sources" in content and content["sources"]
        if has_source and has_sources:
            raise ValueError(
                f"Constraint '{path}' cannot have both 'source' (new) and 'sources' (deprecated) fields"
            )

        rules = []
        for rule_data in content.get("rules", []):
            rules.append(ConstraintRule(
                id=rule_data["id"],
                text=rule_data["text"],
                default_severity=rule_data.get("default_severity", "HIGH"),
                examples=rule_data.get("examples"),
            ))

        # Parse source block (new format) or sources (old format)
        source_block = None
        sources = None
        if has_source:
            source_block = SourceBlock.from_dict(content["source"])
        elif has_sources:
            logger.warning(
                f"Constraint '{content.get('id', path.stem)}': 'sources' is deprecated, "
                "use 'source' block for full content resolution"
            )
            sources = content["sources"]

        # Parse optional agents override
        agents = content.get("agents")
        if agents and not isinstance(agents, list):
            logger.warning(
                f"Constraint '{content.get('id', path.stem)}': 'agents' must be a list, ignoring"
            )
            agents = None

        return cls(
            id=content.get("id", path.stem),
            priority=content.get("priority", 10),
            summary=content.get("summary", ""),
            rules=rules,
            source_path=path,
            script=content.get("script"),
            sources=sources,
            source_block=source_block,
            agents=agents,
        )


@dataclasses.dataclass
class CritiqueIssue:
    """A single issue found by a critic."""
    id: str
    rule_id: str
    severity: str
    location: str
    finding: str
    evidence: str
    suggested_fix: Optional[str] = None
    confidence: float = 0.9


@dataclasses.dataclass
class Critique:
    """Structured critique output from a critic agent."""
    constraint_id: str
    reviewer: str
    iteration: int
    overall: str  # PASS or FAIL
    issues: List[CritiqueIssue]
    approved_sections: List[Dict[str, str]]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "reviewer": self.reviewer,
            "iteration": self.iteration,
            "overall": self.overall,
            "issues": [
                {
                    "id": i.id,
                    "rule_id": i.rule_id,
                    "severity": i.severity,
                    "location": i.location,
                    "finding": i.finding,
                    "evidence": i.evidence,
                    "suggested_fix": i.suggested_fix,
                    "confidence": i.confidence,
                }
                for i in self.issues
            ],
            "approved_sections": self.approved_sections,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Critique":
        issues = []
        for issue_data in d.get("issues", []):
            issues.append(CritiqueIssue(
                id=issue_data.get("id", ""),
                rule_id=issue_data.get("rule_id", ""),
                severity=issue_data.get("severity", "HIGH"),
                location=issue_data.get("location", ""),
                finding=issue_data.get("finding", ""),
                evidence=issue_data.get("evidence", ""),
                suggested_fix=issue_data.get("suggested_fix"),
                confidence=issue_data.get("confidence", 0.9),
            ))
        return cls(
            constraint_id=d.get("constraint_id", ""),
            reviewer=d.get("reviewer", ""),
            iteration=d.get("iteration", 1),
            overall=d.get("overall", "FAIL"),
            issues=issues,
            approved_sections=d.get("approved_sections", []),
            summary=d.get("summary", ""),
        )


@dataclasses.dataclass
class AdjudicationDecision:
    """A single decision in an adjudication."""
    issue_id: str
    constraint: str
    severity: str
    status: str  # pursuing, dismissed
    flagged_by: List[str]
    competing_constraint: Optional[str] = None
    adjudication: Optional[str] = None
    rationale: Optional[str] = None
    guidance: Optional[str] = None


@dataclasses.dataclass
class Adjudication:
    """Structured adjudication output."""
    iteration: int
    status: str  # REWRITE or APPROVED
    tension_analysis: List[Dict[str, str]]
    decisions: List[AdjudicationDecision]
    bill_of_work: str
    critical_pursuing: int = 0
    high_pursuing: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "status": self.status,
            "tension_analysis": self.tension_analysis,
            "decisions": [
                {
                    "issue_id": d.issue_id,
                    "constraint": d.constraint,
                    "severity": d.severity,
                    "status": d.status,
                    "flagged_by": d.flagged_by,
                    "competing_constraint": d.competing_constraint,
                    "adjudication": d.adjudication,
                    "rationale": d.rationale,
                    "guidance": d.guidance,
                }
                for d in self.decisions
            ],
            "termination": {
                "critical_pursuing": self.critical_pursuing,
                "high_pursuing": self.high_pursuing,
            },
            "bill_of_work": self.bill_of_work,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Adjudication":
        decisions = []
        for dec_data in d.get("decisions", []):
            decisions.append(AdjudicationDecision(
                issue_id=dec_data.get("issue_id", ""),
                constraint=dec_data.get("constraint", ""),
                severity=dec_data.get("severity", "HIGH"),
                status=dec_data.get("status", "pursuing"),
                flagged_by=dec_data.get("flagged_by", []),
                competing_constraint=dec_data.get("competing_constraint"),
                adjudication=dec_data.get("adjudication"),
                rationale=dec_data.get("rationale"),
                guidance=dec_data.get("guidance"),
            ))
        termination = d.get("termination", {})
        return cls(
            iteration=d.get("iteration", 1),
            status=d.get("status", "REWRITE"),
            tension_analysis=d.get("tension_analysis", []),
            decisions=decisions,
            bill_of_work=d.get("bill_of_work", ""),
            critical_pursuing=termination.get("critical_pursuing", 0),
            high_pursuing=termination.get("high_pursuing", 0),
        )
