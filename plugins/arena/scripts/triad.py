#!/usr/bin/env python3
"""
Agent Arena (Triad) Orchestrator

File-driven multi-agent orchestration for Claude Code, Codex, and Gemini CLIs.

Fixes from code review:
- Path traversal protection in artifact validation
- Fixed done tracking (per-cycle, not per-turn reset)
- Fixed stagnation detection for single-agent runs
- Added logging framework
- Fixed silent YAML parse failures
- Added input validation for mode/persona names
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, IO

import yaml  # Required for constraint loading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("triad")

EXIT_OK = 0
EXIT_HITL = 10
EXIT_MAX_TURNS = 11  # Changed from 1 to be distinct from generic failure
EXIT_ERROR = 1

DEFAULT_TIMEOUT_SECONDS: Optional[int] = None  # No timeout by default

# Context window settings for thread history
DEFAULT_THREAD_HISTORY_COUNT = 10  # Number of recent messages to include
DEFAULT_MESSAGE_TRUNCATE_LENGTH = 2000  # Characters per message (was 500)

# Valid characters for mode/persona names (security: prevent path traversal)
VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Script location for plugin-relative paths
# When installed as plugin: points to plugin's scripts/ dir
# When run standalone: points to ~/.arena/
SCRIPT_DIR = Path(__file__).parent.resolve()
# Default config dir is sibling to scripts/ in plugin structure, or same dir for standalone
DEFAULT_CONFIG_DIR = (SCRIPT_DIR.parent / "config") if (SCRIPT_DIR.parent / "config").exists() else SCRIPT_DIR

# Add script directory to path for relative imports (enables running from any directory)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Import router for dynamic expert selection
ROUTER_AVAILABLE = False
ROUTER_IMPORT_ERROR: Optional[str] = None
try:
    from router import select_experts, load_experts, save_routing_result
    ROUTER_AVAILABLE = True
except ImportError as e:
    ROUTER_IMPORT_ERROR = str(e)

# Global live log file handle (set by orchestrator)
_live_log: Optional[IO[str]] = None


def write_live(msg: str, prefix: str = "") -> None:
    """Write to live log file for real-time monitoring via tail -f."""
    if _live_log:
        ts = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {prefix}{msg}\n" if prefix else f"[{ts}] {msg}\n"
        _live_log.write(line)
        _live_log.flush()


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_text(path: Path) -> str:
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
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False))


def normalize_for_hash(s: str) -> str:
    return " ".join(s.strip().lower().split())


def sha256(s: str) -> str:
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
    name: str
    kind: str  # claude, codex, gemini
    cmd: List[str]
    timeout: Optional[int] = DEFAULT_TIMEOUT_SECONDS
    suppress_stderr: bool = False  # Don't stream stderr to live log


@dataclasses.dataclass
class Envelope:
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

    @classmethod
    def from_yaml(cls, path: Path) -> "Constraint":
        """Load constraint from YAML file."""
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules = []
        for rule_data in content.get("rules", []):
            rules.append(ConstraintRule(
                id=rule_data["id"],
                text=rule_data["text"],
                default_severity=rule_data.get("default_severity", "HIGH"),
                examples=rule_data.get("examples"),
            ))
        return cls(
            id=content.get("id", path.stem),
            priority=content.get("priority", 10),
            summary=content.get("summary", ""),
            rules=rules,
            source_path=path,
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


# =============================================================================
# Prompt Templates for Reliable Generation
# =============================================================================

def build_generator_prompt(
    goal: str,
    source: str,
    compressed_constraints: str,
    previous_artifact: Optional[str],
    previous_adjudication: Optional[Adjudication],
    iteration: int,
) -> str:
    """Build prompt for the generator phase."""
    refinement_section = ""
    if previous_artifact and previous_adjudication:
        refinement_section = f"""
PREVIOUS ARTIFACT (ITERATION {iteration - 1})
{previous_artifact}

ADJUDICATION FEEDBACK
{previous_adjudication.bill_of_work}

INSTRUCTIONS
You are REFINING the previous artifact. Apply ONLY the fixes specified in the bill of work.
Do NOT introduce new content or restructure unless specifically required by the feedback.
Maintain the original structure and intent while addressing the issues.
"""
    else:
        refinement_section = """
INSTRUCTIONS
Generate initial content that satisfies the goal while adhering to all constraints.
Be thorough and complete - this is your first draft.
"""

    return f"""\
SYSTEM CONTEXT
You are a generator agent in a reliable generation pipeline.
Iteration: {iteration}

GOAL
{goal.strip()}

{f"SOURCE MATERIAL{chr(10)}{source.strip()}" if source else ""}

CONSTRAINTS
{compressed_constraints}

{refinement_section}

OUTPUT
Produce ONLY the artifact content (no JSON envelope, no explanations).
The output should be the complete, final text ready for critique.
""".strip()


def build_critic_prompt(
    constraint: Constraint,
    artifact: str,
    goal: str,
    iteration: int,
) -> str:
    """Build prompt for a critic phase."""
    rules_section = []
    for rule in constraint.rules:
        rule_text = f"### Rule: {rule.id}\n{rule.text}\nDefault Severity: {rule.default_severity}"
        if rule.examples:
            if "violation" in rule.examples:
                rule_text += f"\nExample Violation: {rule.examples['violation']}"
            if "compliant" in rule.examples:
                rule_text += f"\nExample Compliant: {rule.examples['compliant']}"
        rules_section.append(rule_text)

    return f"""\
SYSTEM CONTEXT
You are a critic agent reviewing content for constraint: {constraint.id}
Iteration: {iteration}

CONSTRAINT: {constraint.id.upper()}
Priority: {constraint.priority}

{constraint.summary}

RULES TO EVALUATE
{chr(10).join(rules_section)}

GOAL CONTEXT
{goal[:500]}

ARTIFACT TO REVIEW
{artifact}

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object (no markdown, no extra text):
{{
  "constraint_id": "{constraint.id}",
  "overall": "PASS" | "FAIL",
  "issues": [
    {{
      "id": "{constraint.id}-001",
      "rule_id": "rule-id-that-was-violated",
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
      "location": "paragraph X, sentence Y" or "section name",
      "finding": "What is wrong",
      "evidence": "Quote or reference from rules",
      "suggested_fix": "How to fix it",
      "confidence": 0.0-1.0
    }}
  ],
  "approved_sections": [
    {{"location": "paragraphs 1-5", "note": "Meets all criteria"}}
  ],
  "summary": "Brief summary of findings"
}}

EVALUATION GUIDELINES
- Be thorough but fair - only flag genuine violations
- Provide specific locations for each issue
- Suggest concrete fixes, not vague improvements
- Rate confidence based on clarity of violation
- If no issues found, return overall: "PASS" with empty issues array
""".strip()


def build_adjudicator_prompt(
    constraints: List[Constraint],
    artifact: str,
    critiques: List[Critique],
    goal: str,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build prompt for the adjudicator phase."""
    constraints_section = "\n".join(
        f"- {c.id} (priority {c.priority}): {c.summary[:100]}..."
        for c in constraints
    )

    critiques_section = []
    for critique in critiques:
        critique_text = f"### {critique.reviewer} on {critique.constraint_id}: {critique.overall}"
        if critique.issues:
            for issue in critique.issues:
                critique_text += f"\n  - [{issue.severity}] {issue.id}: {issue.finding}"
        else:
            critique_text += "\n  No issues found"
        critiques_section.append(critique_text)

    return f"""\
SYSTEM CONTEXT
You are the adjudicator in a reliable generation pipeline.
Your role is to find the optimal boundary between competing constraints.
Iteration: {iteration}/{max_iterations}

GOAL
{goal.strip()}

CONSTRAINTS (ordered by priority)
{constraints_section}

ARTIFACT UNDER REVIEW
{artifact}

CRITIQUES FROM ALL REVIEWERS
{chr(10).join(critiques_section)}

YOUR ROLE
1. Analyze tensions between competing constraints
2. Decide which issues to pursue vs dismiss
3. Create a prioritized bill of work for the generator

DECISION CRITERIA
- CRITICAL issues: Must be fixed (safety, security, legal)
- HIGH issues: Should be fixed unless they conflict with higher-priority constraints
- MEDIUM/LOW issues: Fix if easy, dismiss if they conflict or are stylistic
- When constraints conflict: Higher priority wins, but find the boundary that satisfies both maximally

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object:
{{
  "iteration": {iteration},
  "status": "REWRITE" | "APPROVED",
  "tension_analysis": [
    {{
      "axis": "constraint-A vs constraint-B",
      "current_position": "where the artifact currently sits",
      "target": "where it should be",
      "guidance": "how to get there"
    }}
  ],
  "decisions": [
    {{
      "issue_id": "constraint-001",
      "constraint": "constraint-name",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "status": "pursuing" | "dismissed",
      "flagged_by": ["agent1", "agent2"],
      "competing_constraint": "other-constraint or null",
      "adjudication": "reasoning if in tension",
      "rationale": "why dismissed (if dismissed)",
      "guidance": "specific fix instructions"
    }}
  ],
  "termination": {{
    "critical_pursuing": 0,
    "high_pursuing": 0
  }},
  "bill_of_work": "## MUST FIX (CRITICAL)\\n1. issue-id: guidance\\n\\n## SHOULD FIX (HIGH)\\n..."
}}

APPROVAL CRITERIA
- Status should be "APPROVED" only if:
  - No CRITICAL issues pursuing
  - No HIGH issues pursuing (or profile allows some HIGH issues)
- Otherwise status should be "REWRITE"
""".strip()


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


async def run_process(
    cmd: List[str],
    stdin_text: str,
    timeout: Optional[int],
    stream_prefix: Optional[str] = None,
    suppress_stderr: bool = False,
) -> Tuple[int, str, str]:
    """Run subprocess with optional timeout and streaming output.

    Args:
        cmd: Command to run
        stdin_text: Text to send to stdin
        timeout: Timeout in seconds (None = no timeout)
        stream_prefix: If set, stream output to console with this prefix
        suppress_stderr: If True, don't stream stderr (still capture it)

    Returns:
        (returncode, stdout, stderr)
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Write stdin and close
    if proc.stdin:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    async def read_stream(
        stream: asyncio.StreamReader, lines: List[str], is_stderr: bool = False
    ) -> None:
        """Read stream line by line, optionally printing with prefix."""
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
            lines.append(line)
            # Skip streaming stderr if suppressed (still captured in lines)
            if is_stderr and suppress_stderr:
                continue
            if stream_prefix:
                prefix = f"{stream_prefix}"
                if is_stderr:
                    prefix = f"{stream_prefix} [stderr]"
                # Write to live log
                write_live(line, prefix=f"{prefix}: ")
                # Also print to stdout
                print(f"  {prefix}: {line}", flush=True)

    async def run_with_streaming() -> int:
        """Run the process with streaming output."""
        await asyncio.gather(
            read_stream(proc.stdout, stdout_lines, is_stderr=False),
            read_stream(proc.stderr, stderr_lines, is_stderr=True),
        )
        await proc.wait()
        return proc.returncode or 0

    try:
        if timeout:
            rc = await asyncio.wait_for(run_with_streaming(), timeout=timeout)
        else:
            rc = await run_with_streaming()
        return rc, "\n".join(stdout_lines), "\n".join(stderr_lines)
    except asyncio.TimeoutError:
        # Graceful shutdown: try terminate first, then kill
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return -1, "\n".join(stdout_lines), f"Process timed out after {timeout}s"


async def run_agent(
    agent: Agent, prompt: str, stream: bool = True
) -> Tuple[Envelope, str, str]:
    """Run agent CLI and parse response.

    Args:
        agent: Agent to run
        prompt: Prompt to send
        stream: If True, stream output to console with agent name prefix
    """
    stream_prefix = agent.name if stream else None
    rc, stdout, stderr = await run_process(
        agent.cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
    )

    if rc == -1:  # Timeout
        return Envelope.error(f"Timeout after {agent.timeout}s"), stdout, stderr

    if rc != 0 and not stdout.strip():
        return Envelope.error(f"Exit code {rc}: {stderr[:500]}"), stdout, stderr

    # Warn if non-zero exit but has output
    if rc != 0:
        logger.warning(f"Agent {agent.name} exited with code {rc} but produced output")

    env, err = parse_envelope(stdout, agent.kind)
    return env, stdout, stderr


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
        import yaml
        frontmatter = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        return frontmatter, body
    except ImportError:
        logger.warning("PyYAML not installed; frontmatter parsing disabled")
        return {}, content
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
    for key in ["mode", "default_pattern", "order"]:
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


async def run_research(
    topics: List[str],
    research_agent_cmd: List[str],
    goal: str,
    stream: bool = True,
) -> str:
    """Run web research on given topics using the specified agent."""
    research_prompt = f"""\
You are a focused web researcher. Research the following topics thoroughly using web search.

GOAL CONTEXT:
{goal[:500]}

RESEARCH TOPICS:
{chr(10).join(f"- {t}" for t in topics)}

INSTRUCTIONS:
1. Search for each topic using targeted queries
2. Extract specific facts, data, and insights
3. Include source URLs for all findings
4. Focus on actionable information relevant to the goal

OUTPUT FORMAT:
Provide findings as a structured list:
- [Finding] (Source: URL)

Be thorough but concise. No fluff.
"""
    write_live("=" * 40)
    write_live(f"RESEARCH: {', '.join(topics[:3])}")
    write_live("=" * 40)

    rc, stdout, stderr = await run_process(
        research_agent_cmd,
        research_prompt,
        timeout=None,
        stream_prefix="researcher" if stream else None,
    )

    if rc != 0 and not stdout.strip():
        return f"Research failed: {stderr[:500]}"

    return stdout.strip()


def build_prompt(
    agent_name: str,
    mode: str,
    mode_body: str,
    persona_body: str,
    pattern: str,
    turn_idx: int,
    max_turns: int,
    goal: str,
    context: str,
    summary: str,
    thread_tail: List[Dict[str, Any]],
    hitl_answers: Optional[Dict[str, Any]] = None,
    enable_research: bool = False,
) -> str:
    """Build the prompt for an agent."""
    thread_text = "\n".join(
        f"[{m.get('agent', '?')}|{m.get('status', '?')}] {m.get('content', '')[:DEFAULT_MESSAGE_TRUNCATE_LENGTH]}"
        for m in thread_tail[-DEFAULT_THREAD_HISTORY_COUNT:]
    )

    answers_section = ""
    if hitl_answers:
        answers_section = f"""
HUMAN ANSWERS TO PREVIOUS QUESTIONS
{json.dumps(hitl_answers, indent=2)}
"""

    return f"""\
SYSTEM CONTEXT
You are agent "{agent_name}" in a multi-agent orchestration system.
Mode: {mode} | Pattern: {pattern} | Turn: {turn_idx}/{max_turns}

{mode_body}

{persona_body}

GOAL
{goal.strip()}

SHARED CONTEXT
{context.strip()}

ROLLING SUMMARY
{summary.strip() if summary else "(none)"}

CONVERSATION THREAD (recent)
{thread_text if thread_text else "(start of conversation)"}
{answers_section}

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object (no markdown, no extra text):
{{
  "status": "ok" | "needs_human" | "needs_research" | "done" | "error",
  "message": "your response",
  "questions": [  // empty if none
    {{"id": "q1", "question": "...", "priority": "critical|high|normal", "required": true}}
  ],
  "research_topics": [  // empty if none - only when status="needs_research"
    "specific topic to research"
  ],
  "artifacts": [  // empty if none
    {{"path": "relative/path", "description": "what it is"}}
  ],
  "confidence": 0.0-1.0,  // optional
  "agrees_with": ["agent_name"],  // optional, for consensus
  "objections": [  // optional
    {{"target": "agent or idea", "severity": "critical|major|minor", "reason": "why"}}
  ]
}}

- If you need human clarification, set status="needs_human" with questions
- If the goal is fully satisfied, set status="done"
- Include confidence (0.0-1.0) when making assessments
- Use agrees_with to indicate consensus with other agents
{f'- If you need web research to inform your response, set status="needs_research" with research_topics' if enable_research else ''}
""".strip()


def tail_thread(thread_path: Path, n: int = 20) -> List[Dict[str, Any]]:
    """Read last N entries from thread JSONL."""
    if not thread_path.exists():
        return []
    lines = thread_path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-n:]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def detect_stagnation(
    thread_tail: List[Dict[str, Any]], agents: List[str], threshold: float = 0.90
) -> bool:
    """Detect if last two rounds are too similar (stagnation)."""
    # Need at least 2 agents to detect meaningful stagnation
    if len(agents) < 2:
        return False

    # Get last messages per agent for last 2 rounds
    agent_msgs: Dict[str, List[str]] = {a: [] for a in agents}
    for entry in reversed(thread_tail):
        agent = entry.get("agent", "")
        if agent in agent_msgs and len(agent_msgs[agent]) < 2:
            agent_msgs[agent].append(entry.get("content", ""))

    # Need at least 2 messages per agent to compare
    agents_with_history = [a for a, msgs in agent_msgs.items() if len(msgs) >= 2]
    if len(agents_with_history) < 2:
        return False

    # Check similarity for each agent
    for agent in agents_with_history:
        msgs = agent_msgs[agent]
        sim = text_similarity(msgs[0], msgs[1])
        if sim < threshold:
            return False  # Significant change detected

    return True  # All agents stagnated


def check_consensus(envelopes: Dict[str, Envelope], min_agree: int = 2) -> bool:
    """Check for consensus using agrees_with field or message similarity."""
    agents = list(envelopes.keys())
    if len(agents) < 2:
        return False

    # Check explicit agrees_with
    for agent, env in envelopes.items():
        if env.status in ("error", "needs_human"):
            return False
        if env.agrees_with:
            agreers = set(env.agrees_with) | {agent}
            if len(agreers & set(agents)) >= min_agree:
                return True

    # Fallback: check message similarity
    messages = [(a, envelopes[a].message) for a in agents]
    for i, (a1, m1) in enumerate(messages):
        similar_count = 1
        for j, (a2, m2) in enumerate(messages):
            if i != j and text_similarity(m1, m2) > 0.85:
                similar_count += 1
        if similar_count >= min_agree:
            return True

    return False


def ingest_hitl_answers(state_dir: Path) -> Optional[Dict[str, Any]]:
    """Read and consume HITL answers. Returns answers or None."""
    answers_path = state_dir / "hitl" / "answers.json"
    if not answers_path.exists():
        return None

    answers = load_json(answers_path, None)
    if not answers:
        return None

    # Move to processed (don't delete, keep for audit)
    processed_path = state_dir / "hitl" / f"answers_{sha256(utc_now_iso())}.processed.json"
    answers_path.rename(processed_path)

    return answers


def write_hitl_questions(
    state_dir: Path, questions: List[Dict[str, Any]], turn: int
) -> None:
    """Write pending HITL questions and display them to user."""
    qpath = state_dir / "hitl" / "questions.json"
    save_json_atomic(
        qpath,
        {
            "timestamp": utc_now_iso(),
            "turn": turn,
            "questions": questions,
            "answer_format": {
                "answers": [{"question_id": "q1", "answer": "your answer"}]
            },
        },
    )

    # Display questions prominently
    print("\n" + "=" * 60)
    print("HUMAN INPUT NEEDED")
    print("=" * 60)
    write_live("=" * 50)
    write_live("HUMAN INPUT NEEDED")
    write_live("=" * 50)

    for agent_q in questions:
        agent = agent_q.get("agent", "unknown")
        agent_questions = agent_q.get("questions", [])
        print(f"\n[{agent}] asks:")
        write_live(f"\n[{agent}] asks:")

        for i, q in enumerate(agent_questions, 1):
            if isinstance(q, dict):
                q_text = q.get("question", q.get("text", str(q)))
                q_id = q.get("id", f"q{i}")
            else:
                q_text = str(q)
                q_id = f"q{i}"
            print(f"  [{q_id}] {q_text}")
            write_live(f"  [{q_id}] {q_text}")

    print(f"\nTo respond, edit: {qpath.parent / 'answers.json'}")
    print("Format: {\"answers\": [{\"question_id\": \"q1\", \"answer\": \"your answer\"}]}")
    print("Then re-run the orchestrator with the same --name")
    print("=" * 60 + "\n")
    write_live(f"\nEdit {qpath.parent / 'answers.json'} to respond")
    write_live("=" * 50)


def write_agent_result(
    run_dir: Path,
    status: str,
    exit_code: int,
    summary: Optional[str] = None,
    questions: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> None:
    """Write agent-result.json for SubagentStop hook consumption."""
    result = {
        "timestamp": utc_now_iso(),
        "run_name": run_dir.name,
        "status": status,
        "exit_code": exit_code,
        "questions": questions,
        "summary": summary,
    }
    if error:
        result["error"] = error
    save_json_atomic(run_dir / "agent-result.json", result)


def write_resolution(state_dir: Path, reason: str, turn: int, summary: str) -> None:
    """Write final resolution artifact."""
    save_json_atomic(
        state_dir / "resolution.json",
        {
            "timestamp": utc_now_iso(),
            "reason": reason,
            "final_turn": turn,
            "summary": summary,
        },
    )


# =============================================================================
# Multi-Phase Orchestrator (Reliable Generation Pattern)
# =============================================================================

async def run_multi_phase_orchestrator(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
    run_dir: Path,
    agents: Dict[str, Agent],
    phases_config: Dict[str, Any],
) -> int:
    """Run multi-phase orchestration (Generate → Critique → Adjudicate → Refine loop)."""
    thread_path = run_dir / "thread.jsonl"
    state_path = run_dir / "state.json"

    # Load state for resuming
    state = load_json(
        state_path,
        {
            "awaiting_human": False,
            "iteration": 1,
            "phase": "generate",
            "artifact": None,
            "critiques": [],
            "adjudication": None,
        },
    )

    # HITL directory for this run
    hitl_dir = run_dir / "hitl"
    ensure_secure_dir(hitl_dir)

    # Check for HITL resume
    hitl_answers = None
    if state.get("awaiting_human"):
        hitl_answers = ingest_hitl_answers(run_dir)
        if hitl_answers:
            state["awaiting_human"] = False
            # Add answers to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"human:{utc_now_iso()}"),
                    "ts": utc_now_iso(),
                    "iteration": state.get("iteration", 1),
                    "phase": "hitl_response",
                    "agent": "human",
                    "role": "user",
                    "content": json.dumps(hitl_answers),
                },
            )
            save_json_atomic(state_path, state)
            write_live("=" * 60)
            write_live("RESUMING: Human answers received")
            write_live("=" * 60)
        else:
            logger.info(f"Awaiting human answers at {hitl_dir / 'answers.json'}")
            logger.info(f"See questions at {hitl_dir / 'questions.json'}")
            return EXIT_HITL

    # Load inputs
    goal = read_text(run_dir / "goal.md")
    source = read_text(run_dir / "source.md")
    constraints = load_constraints(run_dir / "constraints")

    if not goal.strip():
        logger.error(f"No goal defined in {run_dir / 'goal.md'}")
        return EXIT_ERROR

    if not constraints:
        logger.warning("No constraints found - running without constraint enforcement")
        write_live("WARNING: No constraints/ directory or no .yaml files found")

    # Compress constraints for generator
    compressed = compress_constraints(constraints)
    if compressed:
        save_compressed_constraints(run_dir, compressed)

    # Configuration
    max_iterations = phases_config.get("refine", {}).get("max_iterations", 3)
    if args.max_iterations:
        max_iterations = args.max_iterations

    termination_config = phases_config.get("termination", {})
    approve_when = termination_config.get("approve_when", "no_critical_and_no_high")

    # Agent configuration
    generate_agent_name = phases_config.get("generate", {}).get("agent", "claude")
    adjudicate_agent_name = phases_config.get("adjudicate", {}).get("agent", "claude")
    critique_agents = phases_config.get("critique", {}).get("agents", ["claude", "codex", "gemini"])

    # Validate agents exist
    for agent_name in [generate_agent_name, adjudicate_agent_name] + critique_agents:
        if agent_name not in agents:
            logger.error(f"Agent '{agent_name}' not found in configuration")
            return EXIT_ERROR

    # Log configuration
    write_live("=" * 60)
    write_live(f"RELIABLE GENERATION: {run_dir.name}")
    write_live(f"Goal: {goal[:50]}...")
    constraint_summary = ", ".join(f"{c.id} ({len(c.rules)} rules)" for c in constraints)
    write_live(f"Constraints: {constraint_summary}" if constraints else "Constraints: (none)")
    write_live(f"Max iterations: {max_iterations}")
    write_live("")

    # Dry run mode - just show configuration and structure guidance
    if args.dry_run:
        write_live("DRY RUN - Configuration Preview")
        write_live("-" * 40)
        write_live("")
        write_live("AGENTS:")
        write_live(f"  Generator:   {generate_agent_name}")
        write_live(f"  Critics:     {', '.join(critique_agents)}")
        write_live(f"  Adjudicator: {adjudicate_agent_name}")
        write_live("")
        write_live("INPUTS:")
        write_live(f"  Goal:        {run_dir / 'goal.md'} {'✓' if goal.strip() else '✗ MISSING'}")
        write_live(f"  Source:      {run_dir / 'source.md'} {'✓' if source.strip() else '(optional)'}")
        write_live(f"  Constraints: {run_dir / 'constraints/'}")
        write_live("")

        if constraints:
            write_live("CONSTRAINT ROUTING (all-to-all):")
            total_critiques = len(constraints) * len(critique_agents)
            write_live(f"  {len(constraints)} constraints × {len(critique_agents)} agents = {total_critiques} critique tasks")
            write_live("")
            for constraint in constraints:
                write_live(f"  {constraint.id} (priority {constraint.priority}, {len(constraint.rules)} rules):")
                for agent in critique_agents:
                    write_live(f"    → {agent}")
        else:
            write_live("⚠ NO CONSTRAINTS FOUND")
            write_live("")
            write_live("Expected structure:")
            write_live(f"  {run_dir}/")
            write_live("  ├── goal.md              # What to generate (REQUIRED)")
            write_live("  ├── source.md            # Source material (optional)")
            write_live("  └── constraints/         # Constraint files (REQUIRED)")
            write_live("      ├── safety.yaml      # Example: safety rules")
            write_live("      ├── quality.yaml     # Example: quality standards")
            write_live("      └── tone.yaml        # Example: tone/style rules")
            write_live("")
            write_live("Constraint YAML format:")
            write_live("  id: safety")
            write_live("  priority: 1              # Lower = higher priority")
            write_live("  summary: |")
            write_live("    Brief description for generator...")
            write_live("  rules:")
            write_live("    - id: rule-name")
            write_live("      text: \"Detailed rule for critics\"")
            write_live("      default_severity: CRITICAL  # CRITICAL/HIGH/MEDIUM/LOW")
            write_live("")
            write_live(f"See template: {SCRIPT_DIR.parent / 'templates' / 'reliable-generation' / 'README.md'}")

        write_live("")
        write_live("-" * 40)
        return EXIT_OK

    # Resume from saved state
    iteration = state.get("iteration", 1)
    current_phase = state.get("phase", "generate")
    artifact = state.get("artifact")
    critiques = [Critique.from_dict(c) for c in state.get("critiques", [])]
    adjudication = Adjudication.from_dict(state["adjudication"]) if state.get("adjudication") else None

    # Main iteration loop
    while iteration <= max_iterations:
        iter_dir = run_dir / "iterations" / str(iteration)
        iter_dir.mkdir(parents=True, exist_ok=True)

        # Phase: Generate
        if current_phase == "generate":
            write_live("")
            write_live(f"▶ PHASE: Generation (iteration {iteration})")
            write_live(f"  {generate_agent_name} → generating draft...")

            prompt = build_generator_prompt(
                goal=goal,
                source=source,
                compressed_constraints=compressed,
                previous_artifact=artifact,
                previous_adjudication=adjudication,
                iteration=iteration,
            )

            # Save prompt
            write_text_atomic(iter_dir / f"prompt_generate_{generate_agent_name}.txt", prompt)

            # Run generator
            agent = agents[generate_agent_name]
            rc, stdout, stderr = await run_process(
                agent.cmd, prompt, agent.timeout,
                stream_prefix=generate_agent_name if not args.no_stream else None,
                suppress_stderr=agent.suppress_stderr,
            )

            if rc != 0 and not stdout.strip():
                logger.error(f"Generator failed: {stderr[:500]}")
                return EXIT_ERROR

            artifact = stdout.strip()
            write_text_atomic(iter_dir / "artifact.md", artifact)

            token_count = len(artifact.split())
            write_live(f"  ✓ Draft complete (~{token_count} words)")

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"generator:{utc_now_iso()}:{iteration}"),
                    "ts": utc_now_iso(),
                    "iteration": iteration,
                    "phase": "generate",
                    "agent": generate_agent_name,
                    "role": "assistant",
                    "content": f"Generated artifact ({token_count} words)",
                    "artifact_path": str(iter_dir / "artifact.md"),
                },
            )

            # Update state
            state["artifact"] = artifact
            state["phase"] = "critique"
            state["critiques"] = []
            save_json_atomic(state_path, state)

            current_phase = "critique"

        # Phase: Critique (parallel, all-to-all)
        if current_phase == "critique":
            write_live("")
            write_live("▶ PHASE: Critique (parallel)")

            critiques_dir = iter_dir / "critiques"
            critiques_dir.mkdir(parents=True, exist_ok=True)

            # Build all critique tasks (all agents × all constraints)
            critique_tasks = []
            task_info = []

            for constraint in constraints:
                for agent_name in critique_agents:
                    agent = agents[agent_name]
                    prompt = build_critic_prompt(
                        constraint=constraint,
                        artifact=artifact,
                        goal=goal,
                        iteration=iteration,
                    )

                    write_text_atomic(
                        critiques_dir / f"prompt_{constraint.id}_{agent_name}.txt",
                        prompt,
                    )

                    write_live(f"  {agent_name} ({constraint.id}) → reviewing...")

                    critique_tasks.append(
                        run_process(
                            agent.cmd, prompt, agent.timeout,
                            stream_prefix=f"{agent_name}[{constraint.id}]" if not args.no_stream else None,
                            suppress_stderr=agent.suppress_stderr,
                        )
                    )
                    task_info.append((agent_name, constraint))

            # Run all critiques in parallel
            results = await asyncio.gather(*critique_tasks)

            critiques = []
            for (rc, stdout, stderr), (agent_name, constraint) in zip(results, task_info):
                critique = parse_critique(stdout, agent_name, constraint.id, iteration)
                critiques.append(critique)

                # Save critique output
                save_json_atomic(
                    critiques_dir / f"{constraint.id}-{agent_name}.json",
                    critique.to_dict(),
                )

                # Log summary
                issue_count = len(critique.issues)
                critical_count = sum(1 for i in critique.issues if i.severity == "CRITICAL")
                high_count = sum(1 for i in critique.issues if i.severity == "HIGH")

                if issue_count > 0:
                    write_live(f"  {agent_name} ({constraint.id}): {critical_count} CRITICAL, {high_count} HIGH, {issue_count - critical_count - high_count} other")
                else:
                    write_live(f"  {agent_name} ({constraint.id}): PASS")

                # Append to thread
                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"critique:{agent_name}:{constraint.id}:{utc_now_iso()}"),
                        "ts": utc_now_iso(),
                        "iteration": iteration,
                        "phase": "critique",
                        "agent": agent_name,
                        "constraint": constraint.id,
                        "role": "assistant",
                        "overall": critique.overall,
                        "issues_count": issue_count,
                        "content": critique.summary,
                    },
                )

            # Update state
            state["critiques"] = [c.to_dict() for c in critiques]
            state["phase"] = "adjudicate"
            save_json_atomic(state_path, state)

            current_phase = "adjudicate"

        # Phase: Adjudicate
        if current_phase == "adjudicate":
            write_live("")
            write_live("▶ PHASE: Adjudication")
            write_live(f"  {adjudicate_agent_name} → analyzing critiques...")

            prompt = build_adjudicator_prompt(
                constraints=constraints,
                artifact=artifact,
                critiques=critiques,
                goal=goal,
                iteration=iteration,
                max_iterations=max_iterations,
            )

            # Log context size for monitoring
            context_tokens = len(prompt.split())
            if context_tokens > 100000:  # Warn at ~100K words (rough proxy for tokens)
                write_live(f"  ⚠ Large context: ~{context_tokens} words")

            write_text_atomic(iter_dir / f"prompt_adjudicate_{adjudicate_agent_name}.txt", prompt)

            agent = agents[adjudicate_agent_name]
            rc, stdout, stderr = await run_process(
                agent.cmd, prompt, agent.timeout,
                stream_prefix=adjudicate_agent_name if not args.no_stream else None,
                suppress_stderr=agent.suppress_stderr,
            )

            if rc != 0 and not stdout.strip():
                logger.error(f"Adjudicator failed: {stderr[:500]}")
                return EXIT_ERROR

            adjudication = parse_adjudication(stdout, iteration)
            save_json_atomic(iter_dir / "adjudication.yaml", adjudication.to_dict())

            write_live(f"  Verdict: {adjudication.status}")
            write_live(f"    CRITICAL pursuing: {adjudication.critical_pursuing}")
            write_live(f"    HIGH pursuing: {adjudication.high_pursuing}")

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"adjudication:{utc_now_iso()}:{iteration}"),
                    "ts": utc_now_iso(),
                    "iteration": iteration,
                    "phase": "adjudicate",
                    "agent": adjudicate_agent_name,
                    "role": "assistant",
                    "status": adjudication.status,
                    "critical_pursuing": adjudication.critical_pursuing,
                    "high_pursuing": adjudication.high_pursuing,
                    "content": adjudication.bill_of_work[:500],
                },
            )

            # Check for approval
            if adjudication.status == "APPROVED":
                write_live("")
                write_live("✓ APPROVED: All constraints satisfied")

                # Save final artifact
                final_dir = run_dir / "final"
                final_dir.mkdir(parents=True, exist_ok=True)
                write_text_atomic(final_dir / "artifact.md", artifact)
                write_live(f"  Output: {final_dir / 'artifact.md'}")

                write_resolution(run_dir, "approved", iteration, "All constraints satisfied")
                write_agent_result(run_dir, "done", EXIT_OK, summary="Artifact approved")
                return EXIT_OK

            # Check for thrashing (same issues returning)
            if iteration >= 2:
                prev_adjudication_path = run_dir / "iterations" / str(iteration - 1) / "adjudication.yaml"
                if prev_adjudication_path.exists():
                    prev_adj = Adjudication.from_dict(load_json(prev_adjudication_path, {}))
                    prev_issues = {d.issue_id for d in prev_adj.decisions if d.status == "pursuing"}
                    curr_issues = {d.issue_id for d in adjudication.decisions if d.status == "pursuing"}
                    if prev_issues & curr_issues:
                        overlapping = prev_issues & curr_issues
                        write_live(f"  ⚠ Thrashing detected: {overlapping}")

                        # Check for HITL escalation
                        if "thrashing" in termination_config.get("escalate_on", []):
                            hitl_questions = [{
                                "agent": "orchestrator",
                                "questions": [{
                                    "id": "thrashing",
                                    "question": f"Thrashing detected on issues: {overlapping}. How should we proceed?",
                                    "priority": "critical",
                                    "required": True,
                                }],
                            }]
                            write_hitl_questions(run_dir, hitl_questions, iteration)
                            state["awaiting_human"] = True
                            state["adjudication"] = adjudication.to_dict()
                            save_json_atomic(state_path, state)
                            logger.info("HITL requested due to thrashing")
                            write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
                            return EXIT_HITL

            # Check for conflicting criticals requiring HITL
            if "conflicting_criticals" in termination_config.get("escalate_on", []):
                critical_issues = [d for d in adjudication.decisions if d.severity == "CRITICAL" and d.status == "pursuing"]
                if len(critical_issues) > 1:
                    # Check if any compete
                    for i, issue in enumerate(critical_issues):
                        if issue.competing_constraint:
                            write_live(f"  ⚠ Conflicting CRITICAL issues detected")
                            hitl_questions = [{
                                "agent": "orchestrator",
                                "questions": [{
                                    "id": "conflict",
                                    "question": f"CRITICAL issues conflict: {issue.issue_id} vs {issue.competing_constraint}. Which takes priority?",
                                    "priority": "critical",
                                    "required": True,
                                }],
                            }]
                            write_hitl_questions(run_dir, hitl_questions, iteration)
                            state["awaiting_human"] = True
                            state["adjudication"] = adjudication.to_dict()
                            save_json_atomic(state_path, state)
                            logger.info("HITL requested due to conflicting criticals")
                            write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
                            return EXIT_HITL

            # Update state for next iteration
            state["adjudication"] = adjudication.to_dict()
            state["phase"] = "generate"
            state["iteration"] = iteration + 1
            save_json_atomic(state_path, state)

            iteration += 1
            current_phase = "generate"

            if iteration > max_iterations:
                break

            write_live("")
            write_live(f"▶ PHASE: Refinement ({iteration - 1}/{max_iterations})")
            write_live(f"  {generate_agent_name} → applying fixes...")

    # Max iterations reached without approval
    write_live("")
    write_live(f"⚠ Max iterations ({max_iterations}) reached without approval")

    # Save best artifact to final regardless of escalation
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(final_dir / "artifact.md", artifact)
    write_text_atomic(final_dir / "status.md", f"# Status: MAX_ITERATIONS\n\nReached {max_iterations} iterations without full approval.\n\nRemaining issues:\n{adjudication.bill_of_work if adjudication else 'Unknown'}")

    if "max_iterations" in termination_config.get("escalate_on", []):
        # Escalate to human for decision
        hitl_questions = [{
            "agent": "orchestrator",
            "questions": [{
                "id": "max_iterations",
                "question": f"Max iterations ({max_iterations}) reached. Accept current artifact or continue?",
                "priority": "high",
                "required": True,
            }],
        }]
        write_hitl_questions(run_dir, hitl_questions, iteration)
        state["awaiting_human"] = True
        save_json_atomic(state_path, state)
        write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
        return EXIT_HITL
    else:
        # Just exit with max_turns code (no HITL configured)
        write_resolution(run_dir, "max_iterations", max_iterations, f"Reached max iterations ({max_iterations})")
        write_agent_result(run_dir, "done", EXIT_MAX_TURNS, summary="Max iterations reached")
        return EXIT_MAX_TURNS


async def run_orchestrator(args: argparse.Namespace) -> int:
    """Main orchestrator loop."""
    cfg = load_json(Path(args.config), {})
    state_dir = Path(cfg.get("state_dir", ".arena"))

    # Global dir for shared modes/personas/profiles
    # Priority: config value > DEFAULT_CONFIG_DIR (auto-detected from script location)
    global_dir_str = cfg.get("global_dir")
    if global_dir_str:
        global_dir = Path(global_dir_str).expanduser()
    else:
        global_dir = DEFAULT_CONFIG_DIR

    # Load and merge profile if specified
    if args.profile:
        profile = load_profile(state_dir, args.profile, global_dir)
        if profile:
            logger.info(f"Loaded profile: {args.profile}")
            if profile.get("description"):
                logger.info(f"  {profile['description']}")
            cfg = merge_profile(cfg, profile)

            # Apply profile settings to args (if not overridden on CLI)
            if args.turns is None and "turns" in profile:
                args.turns = profile["turns"]
            if not args.stop_on_consensus and profile.get("stop_on_consensus"):
                args.stop_on_consensus = True
            if not args.stop_on_stagnation and profile.get("stop_on_stagnation"):
                args.stop_on_stagnation = True
            if not args.pattern and "pattern" in profile:
                args.pattern = profile["pattern"]
            if not args.mode and "mode" in profile:
                args.mode = profile["mode"]

    # Default turns if still not set
    if args.turns is None:
        args.turns = 6

    # Acquire lock
    lock = OrchestratorLock(state_dir)
    if not lock.acquire():
        logger.error("Another orchestrator is running. Exiting.")
        return EXIT_ERROR

    try:
        return await _run_orchestrator_locked(args, cfg, state_dir, global_dir)
    finally:
        lock.release()


async def _run_orchestrator_locked(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
) -> int:
    """Orchestrator logic (with lock held)."""
    global _live_log

    ensure_secure_dir(state_dir)

    # Create or use named run directory
    if args.name:
        run_name = args.name
    else:
        run_name = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    run_dir = state_dir / "runs" / run_name
    is_new_run = not run_dir.exists()
    ensure_secure_dir(run_dir)

    # Create/update runs/latest symlink for run discovery
    latest_link = state_dir / "runs" / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(run_name)  # relative symlink within runs/

    # Check for goal.md in run directory
    goal_path = run_dir / "goal.md"
    if is_new_run and not goal_path.exists():
        # Create template goal.md for user to edit
        goal_path.write_text("# Goal\n\nDescribe your objective here.\n", encoding="utf-8")
        logger.info(f"Created {goal_path} - edit it and re-run")
        return EXIT_ERROR

    # Open live log in run directory (append if resuming)
    live_log_path = run_dir / "live.log"
    _live_log = open(live_log_path, "a", encoding="utf-8")

    # Create/update symlink in state_dir root for easy access
    live_link = state_dir / "live.log"
    if live_link.is_symlink() or live_link.exists():
        live_link.unlink()
    live_link.symlink_to(live_log_path.relative_to(state_dir))

    write_live("=" * 60)
    write_live(f"TRIAD ORCHESTRATOR - {run_name}")
    write_live(f"Watch: tail -f {state_dir}/live.log")
    write_live("=" * 60)

    try:
        return await _run_orchestrator_inner(args, cfg, state_dir, global_dir, run_dir)
    finally:
        write_live("=" * 60)
        write_live("ORCHESTRATOR FINISHED")
        write_live("=" * 60)
        _live_log.close()
        _live_log = None


async def _run_orchestrator_inner(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
    run_dir: Path,
) -> int:
    """Inner orchestrator logic."""
    thread_path = run_dir / "thread.jsonl"
    state_path = run_dir / "state.json"

    # Load state (for resuming interrupted runs)
    state = load_json(
        state_path,
        {"awaiting_human": False, "turn": 0, "done_agents": [], "done_cycle": -1},
    )

    # HITL directory for this run
    hitl_dir = run_dir / "hitl"
    ensure_secure_dir(hitl_dir)

    # Check for HITL resume
    hitl_answers = None
    if state.get("awaiting_human"):
        hitl_answers = ingest_hitl_answers(run_dir)
        if hitl_answers:
            state["awaiting_human"] = False
            # Add answers to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"human:{utc_now_iso()}"),
                    "ts": utc_now_iso(),
                    "turn": state["turn"],
                    "agent": "human",
                    "role": "user",
                    "status": "ok",
                    "content": json.dumps(hitl_answers),
                },
            )
            save_json_atomic(state_path, state)
        else:
            logger.info(f"Awaiting human answers at {hitl_dir / 'answers.json'}")
            logger.info(f"See questions at {hitl_dir / 'questions.json'}")
            return EXIT_HITL

    # Load inputs from run directory
    goal = read_text(run_dir / "goal.md")
    context = read_text(run_dir / "context.md")
    summary = read_text(run_dir / "summary.md")

    if not goal.strip():
        logger.error(f"No goal defined in {run_dir / 'goal.md'}")
        return EXIT_ERROR

    # Load mode (checks local .arena/modes/ first, then global ~/.arena/modes/)
    mode_name = args.mode or cfg.get("mode", "collaborative")
    try:
        mode_meta, mode_body = load_mode(state_dir, mode_name, global_dir)
    except ValueError as e:
        logger.error(str(e))
        return EXIT_ERROR

    # Build agents
    agents_cfg = cfg.get("agents", {})
    agents: Dict[str, Agent] = {}
    for name, acfg in agents_cfg.items():
        agents[name] = Agent(
            name=name,
            kind=acfg["kind"],
            cmd=acfg["cmd"],
            timeout=acfg.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            suppress_stderr=acfg.get("suppress_stderr", False),
        )

    order = cfg.get("order", list(agents.keys()))
    if not order:
        logger.error("No agents configured")
        return EXIT_ERROR

    # Validate order against agents
    for agent_name in order:
        if agent_name not in agents:
            logger.error(f"Agent '{agent_name}' in order but not defined in agents")
            return EXIT_ERROR

    # Check for multi-phase pattern (reliable generation)
    phases_config = cfg.get("phases")
    pattern = (
        args.pattern
        or cfg.get("pattern")
        or cfg.get("default_pattern")
    )

    if pattern == "multi-phase" or phases_config:
        logger.info("Multi-phase pattern detected - using reliable generation orchestrator")
        return await run_multi_phase_orchestrator(
            args=args,
            cfg=cfg,
            state_dir=state_dir,
            global_dir=global_dir,
            run_dir=run_dir,
            agents=agents,
            phases_config=phases_config or {},
        )

    # Dynamic routing: select experts based on goal if enabled
    personas_cfg = cfg.get("personas", {})
    routing_enabled = cfg.get("routing", False)

    if routing_enabled and not ROUTER_AVAILABLE:
        # FAIL LOUDLY: routing was requested but router module couldn't be imported
        error_msg = f"Routing enabled but router module failed to import: {ROUTER_IMPORT_ERROR}"
        logger.error(error_msg)
        write_live(f"ERROR: {error_msg}")
        write_live(f"Router should be at: {SCRIPT_DIR / 'router.py'}")
        return EXIT_ERROR

    if routing_enabled and ROUTER_AVAILABLE:
        logger.info("Dynamic routing enabled - selecting experts for goal...")
        write_live("=" * 40)
        write_live("ROUTING: Selecting experts for goal...")
        write_live("=" * 40)

        # Load expert definitions
        experts_dir = global_dir / "experts" if global_dir else DEFAULT_CONFIG_DIR / "experts"
        expert_pool = load_experts(experts_dir)

        if not expert_pool:
            # FAIL LOUDLY: routing requires experts but none were found
            error_msg = f"Routing enabled but no experts found in: {experts_dir}"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            write_live("Expected .yaml files defining expert personas")
            return EXIT_ERROR

        # Run router to select experts
        routing_result = select_experts(
            goal=goal,
            context=context,
            expert_pool=expert_pool,
            mode=mode_name,
            k=len(order),  # Select as many experts as agents
        )

        # Check if routing succeeded - fail loudly if not
        if not routing_result.success:
            error_msg = f"Router failed: {routing_result.error}"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            return EXIT_ERROR

        # Validate routing produced enough results
        if len(routing_result.selected) < len(order):
            error_msg = (
                f"Router selected {len(routing_result.selected)} experts "
                f"but {len(order)} agents need personas"
            )
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            return EXIT_ERROR

        # Save routing decision for auditability
        save_routing_result(routing_result, run_dir)

        # Map selected experts to agents (in order)
        for i, agent_name in enumerate(order):
            if i < len(routing_result.selected):
                personas_cfg[agent_name] = routing_result.selected[i]

        logger.info(f"Router selected: {routing_result.selected} (confidence: {routing_result.confidence})")
        write_live(f"Selected experts: {', '.join(routing_result.selected)}")
        write_live(f"Confidence: {routing_result.confidence}")
        write_live(f"Reasoning: {routing_result.reasoning[:200]}...")
        write_live("=" * 40)

    # Validate persona configuration: either routing populated personas or they were explicitly set
    # This prevents silent fallback to a non-existent "default" persona
    missing_personas = [agent for agent in order if agent not in personas_cfg]
    if missing_personas and not routing_enabled:
        error_msg = (
            f"No personas configured for agents: {missing_personas}. "
            f"Either enable routing (routing: true) or specify personas explicitly in profile."
        )
        logger.error(error_msg)
        write_live(f"ERROR: {error_msg}")
        return EXIT_ERROR

    # Load personas per agent (checks local first, then global)
    # Note: personas_cfg was populated by routing above if enabled
    agent_personas: Dict[str, str] = {}
    for agent_name in order:
        persona_name = personas_cfg.get(agent_name)
        if not persona_name:
            # This shouldn't happen if validation above passed, but be defensive
            error_msg = f"No persona configured for agent '{agent_name}'"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            return EXIT_ERROR
        try:
            _, persona_body = load_persona(state_dir, persona_name, global_dir)
            agent_personas[agent_name] = persona_body
        except ValueError as e:
            # Enhanced error: include where we looked for the persona
            error_msg = f"Persona '{persona_name}' not found for agent '{agent_name}'"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            write_live(f"Searched: {state_dir / 'personas'}, {global_dir / 'personas' if global_dir else 'N/A'}")
            return EXIT_ERROR

    # Pattern priority: CLI > mode > config > "sequential"
    pattern = (
        args.pattern
        or mode_meta.get("default_pattern")
        or cfg.get("default_pattern")
        or "sequential"
    )

    # Research configuration
    enable_research = cfg.get("enable_research", False)
    research_agent_name = cfg.get("research_agent", "gemini")
    research_agent_cmd = agents.get(research_agent_name, agents.get("gemini", Agent("gemini", "gemini", ["gemini"]))).cmd

    start_turn = state.get("turn", 0)
    max_turns = start_turn + args.turns
    cycle_length = len(order)

    for turn in range(start_turn, max_turns):
        state["turn"] = turn
        save_json_atomic(state_path, state)

        turn_dir = run_dir / "turns" / f"turn_{turn + 1:04d}"
        turn_dir.mkdir(parents=True, exist_ok=True)

        thread_tail = tail_thread(thread_path)

        # Calculate current cycle for done tracking
        current_cycle = turn // cycle_length

        if pattern == "sequential":
            # Sequential: one agent per turn
            agent_name = order[turn % cycle_length]
            agent = agents[agent_name]

            prompt = build_prompt(
                agent_name=agent_name,
                mode=mode_name,
                mode_body=mode_body,
                persona_body=agent_personas.get(agent_name, ""),
                pattern=pattern,
                turn_idx=turn + 1,
                max_turns=max_turns,
                goal=goal,
                context=context,
                summary=summary,
                thread_tail=thread_tail,
                hitl_answers=hitl_answers,
                enable_research=enable_research,
            )
            hitl_answers = None  # Clear after first use

            write_text_atomic(turn_dir / f"prompt_{agent_name}.txt", prompt)

            logger.info(f"Turn {turn + 1}: Running {agent_name}...")
            write_live("-" * 40)
            write_live(f"TURN {turn + 1}: {agent_name}")
            write_live("-" * 40)

            env, raw_out, raw_err = await run_agent(
                agent, prompt, stream=not args.no_stream
            )

            write_live(f">>> {agent_name} finished: status={env.status}")

            # Display any questions from the agent (informational, not HITL-blocking)
            if env.questions and env.status != "needs_human":
                write_live(f"\n[{agent_name}] included questions:")
                for i, q in enumerate(env.questions, 1):
                    if isinstance(q, dict):
                        q_text = q.get("question", q.get("text", str(q)))
                    else:
                        q_text = str(q)
                    write_live(f"  - {q_text}")

            write_text_atomic(
                turn_dir / f"out_{agent_name}.json", json.dumps(env.to_dict(), indent=2)
            )
            if raw_err:
                write_text_atomic(turn_dir / f"stderr_{agent_name}.log", raw_err)

            # Validate artifacts (relative to project, not run_dir)
            warnings = validate_artifacts(env, Path.cwd())
            if warnings:
                env.message += f"\n[Warnings: {'; '.join(warnings)}]"

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"{agent_name}:{utc_now_iso()}:{turn}"),
                    "ts": utc_now_iso(),
                    "turn": turn + 1,
                    "agent": agent_name,
                    "role": "assistant",
                    "status": env.status,
                    "content": env.message,
                    "questions": env.questions,
                    "artifacts": [a for a in env.artifacts],
                    "confidence": env.confidence,
                },
            )

            # Handle HITL
            if env.status == "needs_human" and env.questions:
                write_hitl_questions(
                    run_dir,
                    [{"agent": agent_name, "questions": env.questions}],
                    turn + 1,
                )
                state["awaiting_human"] = True
                save_json_atomic(state_path, state)
                logger.info(f"HITL requested by {agent_name}")
                write_agent_result(
                    run_dir, "needs_human", EXIT_HITL,
                    questions=[{"agent": agent_name, "questions": env.questions}]
                )
                return EXIT_HITL

            # Handle research requests
            if env.status == "needs_research" and env.research_topics and enable_research:
                logger.info(f"Research requested by {agent_name}: {env.research_topics}")
                research_results = await run_research(
                    topics=env.research_topics,
                    research_agent_cmd=research_agent_cmd,
                    goal=goal,
                    stream=not args.no_stream,
                )
                # Append research results to thread
                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"researcher:{utc_now_iso()}:{turn}"),
                        "ts": utc_now_iso(),
                        "turn": turn + 1,
                        "agent": "researcher",
                        "role": "system",
                        "status": "ok",
                        "content": f"RESEARCH RESULTS for: {', '.join(env.research_topics)}\n\n{research_results}",
                        "research_topics": env.research_topics,
                    },
                )
                write_live(f">>> Research complete, {len(research_results)} chars")
                # Don't count this as a turn completion - the agent will continue next turn

            # Track done status per-cycle (not per-turn)
            if state.get("done_cycle") != current_cycle:
                # New cycle: reset done tracking
                state["done_agents"] = []
                state["done_cycle"] = current_cycle

            if env.status == "done":
                done_agents = set(state.get("done_agents", []))
                done_agents.add(agent_name)
                state["done_agents"] = list(done_agents)

                # Check if all agents have said done in this cycle
                if done_agents >= set(order):
                    write_resolution(run_dir, "all_done", turn + 1, env.message)
                    logger.info("All agents reported done. Stopping.")
                    write_agent_result(
                        run_dir, "done", EXIT_OK,
                        summary="All agents reported done"
                    )
                    return EXIT_OK

        else:  # parallel
            # Parallel: all agents run concurrently
            tasks = []
            prompts: Dict[str, str] = {}

            for agent_name in order:
                agent = agents[agent_name]
                prompt = build_prompt(
                    agent_name=agent_name,
                    mode=mode_name,
                    mode_body=mode_body,
                    persona_body=agent_personas.get(agent_name, ""),
                    pattern=pattern,
                    turn_idx=turn + 1,
                    max_turns=max_turns,
                    goal=goal,
                    context=context,
                    summary=summary,
                    thread_tail=thread_tail,
                    hitl_answers=hitl_answers,
                    enable_research=enable_research,
                )
                prompts[agent_name] = prompt
                write_text_atomic(turn_dir / f"prompt_{agent_name}.txt", prompt)
                tasks.append(run_agent(agent, prompt, stream=not args.no_stream))

            hitl_answers = None

            logger.info(f"Turn {turn + 1}: Running all agents in parallel...")
            write_live("-" * 40)
            write_live(f"TURN {turn + 1}: PARALLEL ({', '.join(order)})")
            write_live("-" * 40)

            results = await asyncio.gather(*tasks)

            envelopes: Dict[str, Envelope] = {}
            hitl_questions: List[Dict[str, Any]] = []

            for agent_name, (env, raw_out, raw_err) in zip(order, results):
                write_live(f">>> {agent_name} finished: status={env.status}")
                envelopes[agent_name] = env

                # Display any questions from the agent (informational, not HITL-blocking)
                if env.questions and env.status != "needs_human":
                    write_live(f"\n[{agent_name}] included questions:")
                    for i, q in enumerate(env.questions, 1):
                        if isinstance(q, dict):
                            q_text = q.get("question", q.get("text", str(q)))
                        else:
                            q_text = str(q)
                        write_live(f"  - {q_text}")

                write_text_atomic(
                    turn_dir / f"out_{agent_name}.json",
                    json.dumps(env.to_dict(), indent=2),
                )
                if raw_err:
                    write_text_atomic(turn_dir / f"stderr_{agent_name}.log", raw_err)

                # Validate artifacts (relative to project)
                warnings = validate_artifacts(env, Path.cwd())
                if warnings:
                    env.message += f"\n[Warnings: {'; '.join(warnings)}]"

                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"{agent_name}:{utc_now_iso()}:{turn}"),
                        "ts": utc_now_iso(),
                        "turn": turn + 1,
                        "agent": agent_name,
                        "role": "assistant",
                        "status": env.status,
                        "content": env.message,
                        "questions": env.questions,
                        "artifacts": [a for a in env.artifacts],
                        "confidence": env.confidence,
                        "agrees_with": env.agrees_with,
                    },
                )

                if env.status == "needs_human" and env.questions:
                    hitl_questions.append(
                        {"agent": agent_name, "questions": env.questions}
                    )

            # Handle HITL (collected from all agents)
            if hitl_questions:
                write_hitl_questions(run_dir, hitl_questions, turn + 1)
                state["awaiting_human"] = True
                save_json_atomic(state_path, state)
                logger.info(f"HITL requested by {len(hitl_questions)} agent(s)")
                write_agent_result(
                    run_dir, "needs_human", EXIT_HITL,
                    questions=hitl_questions
                )
                return EXIT_HITL

            # Check consensus
            if args.stop_on_consensus and check_consensus(envelopes):
                write_resolution(
                    run_dir, "consensus", turn + 1, "Agents reached consensus"
                )
                logger.info("Consensus detected. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="Agents reached consensus"
                )
                return EXIT_OK

            # Check if all done
            if all(e.status == "done" for e in envelopes.values()):
                write_resolution(
                    run_dir, "all_done", turn + 1, "All agents reported done"
                )
                logger.info("All agents reported done. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="All agents reported done"
                )
                return EXIT_OK

            # Add round summary to thread
            summary_lines = [
                f"- {a}: {e.status} (conf={e.confidence})" for a, e in envelopes.items()
            ]
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"moderator:{utc_now_iso()}:{turn}"),
                    "ts": utc_now_iso(),
                    "turn": turn + 1,
                    "agent": "moderator",
                    "role": "system",
                    "status": "ok",
                    "content": "ROUND SUMMARY\n" + "\n".join(summary_lines),
                },
            )

        # Check stagnation (after turn 2+)
        if turn >= 2 and args.stop_on_stagnation:
            thread_tail = tail_thread(thread_path, n=cycle_length * 3)
            if detect_stagnation(thread_tail, order):
                write_resolution(
                    run_dir, "stagnation", turn + 1, "No significant progress detected"
                )
                logger.info("Stagnation detected. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="Stagnation detected - no significant progress"
                )
                return EXIT_OK

    # Max turns reached
    write_resolution(run_dir, "max_turns", max_turns, "Reached maximum turn limit")
    logger.info(f"Reached max turns ({max_turns}).")
    write_agent_result(
        run_dir, "done", EXIT_MAX_TURNS,
        summary="Reached maximum turn limit"
    )
    return EXIT_MAX_TURNS


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent Arena (Triad) Orchestrator")
    ap.add_argument("--config", default="triad.config.json", help="Config file path")
    ap.add_argument(
        "--name", "-n",
        help="Run name (creates .arena/runs/<name>/). If not set, uses timestamp."
    )
    ap.add_argument(
        "--profile", "-p",
        help="Load profile (e.g., security-audit, code-review, brainstorm)"
    )
    ap.add_argument("--mode", help="Override mode (e.g., adversarial, collaborative)")
    ap.add_argument(
        "--pattern", choices=["sequential", "parallel", "multi-phase"], help="Override pattern"
    )
    ap.add_argument("--turns", type=int, default=None, help="Number of turns to run")
    ap.add_argument(
        "--max-iterations", type=int, default=None,
        help="Max iterations for multi-phase pattern (default: 3)"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Preview constraint routing without executing (multi-phase only)"
    )
    ap.add_argument(
        "--template-info", action="store_true",
        help="Show reliable-generation template documentation and exit"
    )
    ap.add_argument(
        "--stop-on-consensus", action="store_true", help="Stop on 2-of-3 consensus"
    )
    ap.add_argument(
        "--stop-on-stagnation", action="store_true", help="Stop if no progress"
    )
    ap.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    ap.add_argument(
        "--no-stream", action="store_true", help="Disable streaming output"
    )
    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Handle --template-info flag
    if args.template_info:
        template_readme = DEFAULT_CONFIG_DIR.parent / "templates" / "reliable-generation" / "README.md"
        if template_readme.exists():
            print(template_readme.read_text(encoding="utf-8"))
        else:
            print(f"Template README not found at: {template_readme}")
            print("\nExpected location: triad-plugin/templates/reliable-generation/README.md")
        return EXIT_OK

    return asyncio.run(run_orchestrator(args))


if __name__ == "__main__":
    raise SystemExit(main())
