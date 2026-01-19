# Reliable Generation Template

This template implements constraint-driven generation with iterative refinement.

## Quick Start

```bash
# 1. Create a new run
python3 ~/.arena/arena.py --name my-generation -p reliable-generation

# 2. Copy template files to your run
cp -r ~/.arena/templates/reliable-generation/* .arena/runs/my-generation/

# 3. Edit goal.yaml and constraints, then run
python3 ~/.arena/arena.py --name my-generation -p reliable-generation
```

---

## Directory Structure

```
.arena/runs/<name>/
├── goal.yaml                    # REQUIRED: What to generate + source
├── adjudication-config.yaml     # OPTIONAL: Adjudication rules
├── constraints/                 # REQUIRED: At least one constraint file
│   ├── safety.yaml              # Example: safety rules
│   ├── quality.yaml             # Example: quality standards
│   └── tone.yaml                # Example: tone/style rules
├── .cache/                      # AUTO-GENERATED (gitignored)
│   └── constraints-compressed.md
├── iterations/                  # AUTO-GENERATED: Phase outputs
│   └── 1/
│       ├── artifact.md          # Generated content
│       ├── critiques/           # Critic outputs
│       └── adjudication.yaml    # Adjudicator verdict
├── final/                       # AUTO-GENERATED: Approved output
│   └── artifact.md
├── state.json                   # AUTO-GENERATED: Run state
├── thread.jsonl                 # AUTO-GENERATED: Conversation log
└── live.log                     # AUTO-GENERATED: Real-time progress
```

---

## Input Files

### goal.yaml (REQUIRED)

Describes what you want to generate and optionally includes source material. Be specific about:
- The type of output (story, documentation, code, etc.)
- Requirements and acceptance criteria
- Target audience
- Desired format and length

**Example:**
```yaml
goal: |
  Generate a bedtime story for children ages 4-6.

  ## Requirements

  1. Story should be 500-800 words
  2. Feature a friendly animal protagonist
  3. Include a gentle moral lesson about sharing
  4. End with the character falling asleep peacefully

  ## Output Format

  Single narrative in prose form, suitable for reading aloud.

  ## Success Criteria

  - Engaging opening that captures attention
  - Age-appropriate vocabulary
  - Positive resolution
  - Calming ending suitable for bedtime

# Optional source material (embedded in goal.yaml)
source:
  inline: |
    ## Character Background
    The protagonist is "Ollie the Owl" - a curious young owl who lives
    in a cozy tree hollow with his family.

    ## Setting
    A peaceful forest at dusk, with fireflies and gentle sounds.

    ## Style Reference
    Similar tone to "Goodnight Moon" - repetitive, soothing, rhythmic.
```

**Source block options:**
- `inline:` - Inline text content
- `files:` - List of file paths (supports `{{project_root}}`, `{{run_dir}}`)
- `globs:` - List of glob patterns to match files
- `scripts:` - List of shell commands to execute

### adjudication-config.yaml (OPTIONAL)

Controls how the adjudicator evaluates critiques and decides on approval. **If not provided, the adjudicator uses sensible defaults and infers appropriate behavior from the goal and constraints.**

Default behavior when not specified:
- Block approval on CRITICAL and HIGH severity issues
- Escalate to human on max iterations, thrashing, or conflicting criticals
- Use constraint priorities to resolve conflicts

**When to customize:**
- You want to allow HIGH issues to pass (e.g., stylistic flexibility)
- You have known tension axes the adjudicator should balance
- You need domain-specific instructions

**Schema:**
```yaml
# Approval criteria - when to mark artifact as approved
approval:
  # Block approval if any issues at these severities remain
  block_on: [CRITICAL, HIGH]

  # Or use named policy:
  # policy: no_critical           # Approve if no CRITICAL
  # policy: no_critical_or_high   # Approve if no CRITICAL or HIGH
  # policy: all_resolved          # Approve only if all issues resolved

# HITL escalation - when to ask human for help
escalation:
  triggers:
    - max_iterations        # Reached iteration limit without approval
    - thrashing             # Same issue returns after 2+ fix attempts
    - conflicting_criticals # Fixing one CRITICAL breaks another

# Tension guidance - help adjudicator balance competing constraints
tensions:
  - axis: "engagement vs safety"
    guidance: |
      Maximize engagement without crossing into harmful content.
      When in doubt, prefer safety over excitement.
    winner_on_conflict: safety  # Which constraint wins if irreconcilable

# Custom adjudicator instructions
instructions: |
  Additional context for the adjudicator.
  Example: "This is for a children's app, err on side of caution."
```

**Example - Marketing Copy (allow more flexibility):**
```yaml
approval:
  block_on: [CRITICAL]  # Allow HIGH issues for creative flexibility
escalation:
  triggers: [max_iterations, conflicting_criticals]
tensions:
  - axis: "persuasion vs accuracy"
    guidance: Never sacrifice accuracy for persuasion
    winner_on_conflict: accuracy
instructions: |
  For enterprise buyers. Avoid hype words.
  Focus on concrete benefits over emotional appeals.
```

---

## Constraint Files

Constraints are YAML files in the `constraints/` directory. Each constraint defines:
- **id**: Unique identifier
- **priority**: Lower number = higher priority (1 = highest)
- **summary**: Brief description for the generator
- **rules**: Detailed rules for critics to evaluate

### Constraint Schema

```yaml
# constraints/example.yaml

id: example-constraint      # Unique identifier (alphanumeric, hyphens)
priority: 5                 # 1 = highest priority, 10+ = lower priority

summary: |
  Brief description of what this constraint ensures.
  This text is shown to the generator agent.
  Keep it concise but complete.

rules:
  - id: rule-identifier     # Unique within this constraint
    text: |
      Detailed description of the rule for critics to evaluate.
      Be specific about what constitutes a violation.
    default_severity: CRITICAL  # CRITICAL, HIGH, MEDIUM, or LOW
    examples:                   # Optional but recommended
      violation: "Example of content that violates this rule"
      compliant: "Example of content that follows this rule"

  - id: another-rule
    text: "Another rule description"
    default_severity: HIGH
```

### Severity Levels

| Level | Meaning | Adjudicator Behavior |
|-------|---------|---------------------|
| **CRITICAL** | Must fix - safety, legal, security issues | Always pursued, blocks approval |
| **HIGH** | Should fix - significant quality issues | Usually pursued unless conflicts |
| **MEDIUM** | Consider fixing - moderate issues | Fixed if easy, may dismiss |
| **LOW** | Nice to fix - minor/stylistic issues | Often dismissed if conflicts |

### Priority System

Constraints are evaluated in priority order (lower number = higher priority):

```yaml
# Priority 1: Safety constraints (highest)
# Priority 2-4: Core requirements
# Priority 5-7: Quality standards
# Priority 8-10: Style/polish
```

When constraints conflict, higher priority wins.

---

## Sample Constraints

### Safety Constraint (Priority 1)

```yaml
id: safety
priority: 1

summary: |
  Content must be safe and appropriate for the target audience.
  No harmful content. Fear/conflict must resolve positively.

rules:
  - id: no-harmful-content
    text: |
      Content must not include instructions for harmful activities,
      violence, or dangerous behaviors.
    default_severity: CRITICAL

  - id: positive-resolution
    text: |
      Any concerning scenarios (fear, conflict, danger) must resolve
      positively within the same section or shortly after.
    default_severity: HIGH
    examples:
      violation: "Story ends with child lost and scared"
      compliant: "Child finds way home, feels safe and happy"
```

### Quality Constraint (Priority 5)

```yaml
id: quality
priority: 5

summary: |
  Output must be well-structured, coherent, and polished.
  Clear writing, logical flow, complete coverage.

rules:
  - id: coherent-structure
    text: |
      Content must have clear, logical structure.
      Ideas should flow naturally from one to the next.
    default_severity: HIGH

  - id: completeness
    text: |
      Content must fully address the stated goal.
      No significant aspects should be omitted.
    default_severity: HIGH
```

### Tone Constraint (Priority 10)

```yaml
id: tone
priority: 10

summary: |
  Maintain consistent, appropriate tone throughout.
  Style should match the intended purpose and audience.

rules:
  - id: consistent-voice
    text: |
      Maintain consistent voice and perspective throughout.
      Avoid jarring shifts in tone or style.
    default_severity: MEDIUM

  - id: audience-appropriate
    text: |
      Tone should match target audience expectations.
    default_severity: HIGH
```

---

## CLI Options

```bash
# Create/run with profile
python3 ~/.arena/arena.py --name <name> -p reliable-generation

# Preview constraint routing (no execution)
python3 ~/.arena/arena.py --name <name> -p reliable-generation --dry-run

# Override max iterations (default: 3)
python3 ~/.arena/arena.py --name <name> -p reliable-generation --max-iterations 5

# Disable streaming output
python3 ~/.arena/arena.py --name <name> -p reliable-generation --no-stream

# Verbose logging
python3 ~/.arena/arena.py --name <name> -p reliable-generation -v
```

---

## Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                    ITERATION LOOP                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. GENERATE                                                │
│     └── Claude reads goal + compressed constraints          │
│         └── Produces artifact.md                            │
│                                                             │
│  2. CRITIQUE (parallel)                                     │
│     ├── Claude reviews all constraints                      │
│     ├── Codex reviews all constraints                       │
│     └── Gemini reviews all constraints                      │
│         └── Each produces structured critique JSON          │
│                                                             │
│  3. ADJUDICATE                                              │
│     └── Claude analyzes all critiques                       │
│         ├── Resolves conflicts between constraints          │
│         ├── Decides which issues to pursue vs dismiss       │
│         └── Produces bill_of_work for generator             │
│                                                             │
│  4. DECISION                                                │
│     ├── APPROVED → Save to final/artifact.md, exit          │
│     ├── REWRITE → Loop back to GENERATE with feedback       │
│     └── MAX_ITERATIONS → Escalate to human (HITL)           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## HITL (Human-in-the-Loop)

The system escalates to human intervention when:

1. **Max iterations reached** without approval
2. **Thrashing detected** (same issues keep returning)
3. **Conflicting CRITICAL issues** that can't be auto-resolved

When HITL triggers:
```bash
# System exits with code 10
# Questions written to: .arena/runs/<name>/hitl/questions.json
# To resume, create: .arena/runs/<name>/hitl/answers.json

# answers.json format:
{"answers": [{"question_id": "q1", "answer": "your decision here"}]}

# Then re-run:
python3 ~/.arena/arena.py --name <name> -p reliable-generation
```

---

## Output Files

After a successful run:

| File | Description |
|------|-------------|
| `final/artifact.md` | The approved output |
| `iterations/N/artifact.md` | Draft from iteration N |
| `iterations/N/critiques/*.json` | Critique outputs |
| `iterations/N/adjudication.yaml` | Adjudicator verdict |
| `resolution.json` | How the run ended (approved/max_iterations) |
| `thread.jsonl` | Full conversation log |
| `live.log` | Real-time progress log |

---

## Customization

### Custom Profile

Create `~/.arena/profiles/my-generation.json`:

```json
{
  "description": "My custom generation profile",
  "pattern": "multi-phase",
  "phases": {
    "generate": {
      "agent": "claude"
    },
    "critique": {
      "pattern": "parallel",
      "routing": "all-to-all",
      "agents": ["claude", "codex", "gemini"]
    },
    "adjudicate": {
      "agent": "claude"
    },
    "refine": {
      "max_iterations": 5
    }
  },
  "termination": {
    "approve_when": "no_critical_and_no_high",
    "escalate_on": ["max_iterations", "thrashing"]
  }
}
```

### Per-Project Constraints

Create project-local constraints in `.arena/runs/<name>/constraints/` - these are specific to that run.

### Global Constraints

For constraints shared across projects, create `~/.arena/constraints/` and symlink or copy to runs.
