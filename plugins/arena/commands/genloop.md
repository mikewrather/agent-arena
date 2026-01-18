---
description: Constraint-driven content generation with iterative refinement
argument-hint: [<name> --goal "..." | --help]
allowed-tools: Task, Read, Write, Edit, Bash, Glob, AskUserQuestion
---

# Reliable Generation

Generate content with constraint-based quality assurance using the Generate → Critique → Adjudicate → Refine loop.

## Workflow

### Phase 1: Parse Arguments

Extract from `$ARGUMENTS`:
- `--help` or no arguments → **Discovery Mode** (return comprehensive docs)
- `<name> --goal "..."` → **Inline Mode** (programmatic execution)
- `<name> --setup` → **Setup Mode** (interactive file creation)
- `<name> --run` → **Run Mode** (execute existing files)
- `<name> --dry-run` → **Dry Run Mode** (preview routing)

### Phase 2: Discovery Mode (no args or `--help`)

When called without arguments or with `--help`, return comprehensive documentation for programmatic callers.

**Output the following markdown verbatim:**

```markdown
# Reliable Generation - Command Reference

## Quick Start

```
/arena:genloop my-story \
  --goal "Generate a bedtime story for ages 4-6 about sharing" \
  --constraint "safety: No unresolved fear, age-appropriate" \
  --constraint "quality: Complete narrative arc"
```

With custom adjudication (optional):
```
/arena:genloop my-story \
  --goal "Generate a bedtime story for ages 4-6 about sharing" \
  --constraint "safety: No unresolved fear, age-appropriate" \
  --constraint "quality: Complete narrative arc" \
  --adjudication "approve_when: no_critical"
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `<name>` | Yes | Run name (creates `.arena/runs/<name>/`) |
| `--goal "text"` | Yes* | What to generate |
| `--constraint "id: summary"` | Yes* | Simple constraint (repeatable) |
| `--constraint-yaml "yaml"` | No | Full YAML constraint (repeatable) |
| `--adjudication "key: value"` | No | Override default adjudication behavior |
| `--adjudication-yaml "yaml"` | No | Full YAML adjudication config |
| `--source "text"` | No | Source material/context |
| `--max-iterations N` | No | Override max iterations (default: 3) |

*Required for inline mode. For file-based mode, use `--setup` then `--run`.

**Note:** Adjudication config is optional. The adjudicator infers appropriate behavior from constraints.

## Constraint Format

### Simple (--constraint)

```
--constraint "id: summary text"
```

Creates a basic constraint with one HIGH-severity rule.

### Full YAML (--constraint-yaml)

```yaml
id: safety                    # Unique identifier
priority: 1                   # Lower = higher priority (1-10)

summary: |
  Brief description shown to the generator agent.
  Keep concise but complete.

rules:
  - id: no-harmful-content    # Unique within constraint
    text: |
      Detailed rule text for critics to evaluate against.
      Be specific about what constitutes a violation.
    default_severity: CRITICAL  # CRITICAL | HIGH | MEDIUM | LOW
    examples:
      violation: "Example of bad content"
      compliant: "Example of good content"

  - id: positive-resolution
    text: "Fear/conflict must resolve positively within the scene"
    default_severity: HIGH
```

### Severity Levels

| Level | Meaning | Adjudicator Behavior |
|-------|---------|---------------------|
| CRITICAL | Must fix (safety, legal, security) | Always blocks approval |
| HIGH | Should fix (significant quality) | Usually blocks approval |
| MEDIUM | Consider fixing (moderate issues) | Fixed if easy, may dismiss |
| LOW | Nice to fix (stylistic) | Often dismissed |

### Priority System

- Priority 1-3: Safety/legal constraints (highest)
- Priority 4-6: Core requirements
- Priority 7-9: Quality standards
- Priority 10+: Style/polish (lowest)

When constraints conflict, higher priority (lower number) wins.

## Adjudication Format (OPTIONAL)

**Adjudication config is optional.** The adjudicator uses sensible defaults and infers appropriate behavior from the goal and constraints:
- Blocks approval on CRITICAL and HIGH severity issues
- Escalates to human on max iterations, thrashing, or conflicting criticals
- Uses constraint priorities to resolve conflicts

**When to customize:**
- You want to allow HIGH issues to pass (e.g., stylistic flexibility)
- You have known tension axes the adjudicator should balance
- You need domain-specific instructions

### Simple (--adjudication)

```
--adjudication "approve_when: no_critical"
--adjudication "escalate_on: thrashing"
```

### Full YAML (--adjudication-yaml)

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
    - max_iterations      # Reached iteration limit without approval
    - thrashing           # Same issue returns after 2+ fix attempts
    - conflicting_criticals  # Fixing one CRITICAL breaks another

# Tension guidance - help adjudicator balance competing constraints
tensions:
  - axis: "engagement vs safety"
    guidance: |
      Maximize engagement without crossing into harmful content.
      When in doubt, prefer safety over excitement.
    winner_on_conflict: safety  # Which constraint wins if irreconcilable

  - axis: "completeness vs conciseness"
    guidance: |
      Cover all requirements but avoid unnecessary verbosity.
      Omit only if truly redundant.

# Custom adjudicator instructions
instructions: |
  Additional context or rules for the adjudicator.
  For example: "This is for a children's app, err on side of caution."
```

## Examples

### Story Generation

```
/arena:genloop bedtime-story \
  --goal "Generate a 500-word bedtime story about a bunny learning to share" \
  --constraint "safety: No unresolved fear, violence, or harmful content" \
  --constraint "quality: Complete narrative arc with beginning, middle, end" \
  --constraint "tone: Warm, soothing, suitable for ages 4-6" \
  --adjudication "approve_when: no_critical_or_high"
```

### API Documentation

```
/arena:genloop api-docs \
  --goal "Generate REST API documentation for the /users endpoint" \
  --source "See src/routes/users.ts for implementation" \
  --constraint-yaml "
id: accuracy
priority: 1
summary: All code examples must be correct and runnable
rules:
  - id: valid-syntax
    text: Code examples must have valid syntax for the specified language
    default_severity: CRITICAL
  - id: correct-endpoints
    text: HTTP methods and paths must match the actual API
    default_severity: CRITICAL
" \
  --constraint-yaml "
id: completeness
priority: 2
summary: Document all parameters, responses, and error codes
rules:
  - id: all-params
    text: Every parameter must include type, description, and required status
    default_severity: HIGH
  - id: all-responses
    text: All response codes (200, 400, 401, 404, 500) must be documented
    default_severity: HIGH
"
```

### With Custom Adjudication

```
/arena:genloop marketing-copy \
  --goal "Generate landing page copy for a B2B SaaS product" \
  --constraint "accuracy: No false claims or misleading statistics" \
  --constraint "tone: Professional but approachable, not salesy" \
  --constraint "cta: Clear call-to-action in every section" \
  --adjudication-yaml "
approval:
  block_on: [CRITICAL]  # Allow HIGH issues for stylistic flexibility
escalation:
  triggers: [max_iterations, conflicting_criticals]
tensions:
  - axis: persuasion vs accuracy
    guidance: Never sacrifice accuracy for persuasion
    winner_on_conflict: accuracy
instructions: |
  This is for enterprise buyers. Avoid hype words like
  'revolutionary' or 'game-changing'. Focus on concrete benefits.
"
```

## Output Structure

```
.arena/runs/<name>/
├── goal.md                      # Generation goal
├── source.md                    # Source material (if provided)
├── adjudication-config.yaml     # Adjudication rules
├── constraints/                 # Constraint files
│   ├── safety.yaml
│   └── quality.yaml
├── iterations/                  # Per-iteration outputs
│   └── 1/
│       ├── artifact.md          # Generated content
│       ├── critiques/           # Critic outputs (JSON)
│       └── adjudication.yaml    # Verdict + bill of work
└── final/
    └── artifact.md              # Approved output
```

## Workflow Phases

```
┌─────────────────────────────────────────────────────────────┐
│                    ITERATION LOOP                           │
├─────────────────────────────────────────────────────────────┤
│  1. GENERATE                                                │
│     └── Claude reads goal + constraints summary             │
│         └── Produces artifact.md                            │
│                                                             │
│  2. CRITIQUE (parallel, all-to-all)                         │
│     ├── Claude reviews ALL constraints                      │
│     ├── Codex reviews ALL constraints                       │
│     └── Gemini reviews ALL constraints                      │
│         └── Each produces structured critique JSON          │
│                                                             │
│  3. ADJUDICATE                                              │
│     └── Claude analyzes all critiques                       │
│         ├── Applies adjudication-config rules               │
│         ├── Resolves conflicts using tension guidance       │
│         └── Produces bill_of_work for refinement            │
│                                                             │
│  4. DECISION                                                │
│     ├── APPROVED → Save to final/, exit success             │
│     ├── REWRITE → Loop back to GENERATE with feedback       │
│     └── ESCALATE → Exit with HITL questions                 │
└─────────────────────────────────────────────────────────────┘
```

## Monitoring

```bash
# Watch progress
tail -f .arena/runs/<name>/live.log

# Check current iteration
cat .arena/runs/<name>/state.json

# View latest critique
cat .arena/runs/<name>/iterations/*/critiques/*.json

# View adjudication verdict
cat .arena/runs/<name>/iterations/*/adjudication.yaml
```
```

**End of discovery output.**

### Phase 3: Inline Mode (when `--goal` provided)

Primary mode for agent/programmatic callers.

1. **Create run directory:**
   ```bash
   mkdir -p .arena/runs/<name>/constraints
   ```

2. **Write goal.md:**
   ```
   Write(".arena/runs/<name>/goal.md", "# Goal\n\n<goal_text>")
   ```

3. **Write source.md (if `--source` provided):**
   ```
   Write(".arena/runs/<name>/source.md", "# Source Material\n\n<source_text>")
   ```

4. **Write constraint files:**

   For `--constraint "id: summary"`:
   ```yaml
   # .arena/runs/<name>/constraints/<id>.yaml
   id: <id>
   priority: <auto-increment from 1>
   summary: |
     <summary text>
   rules:
     - id: <id>-main
       text: "<summary text>"
       default_severity: HIGH
   ```

   For `--constraint-yaml`:
   ```
   # Parse YAML to extract id, write directly
   Write(".arena/runs/<name>/constraints/<id>.yaml", <yaml_content>)
   ```

5. **Write adjudication config (if provided):**

   **Skip this step if no adjudication args provided** - the adjudicator will use sensible defaults and infer behavior from constraints.

   For `--adjudication "key: value"` (can appear multiple times):
   ```yaml
   # Parse key-value pairs into YAML structure
   # .arena/runs/<name>/adjudication-config.yaml
   approval:
     policy: <value if key is approve_when>
   escalation:
     triggers: [<value if key is escalate_on>]
   ```

   For `--adjudication-yaml`:
   ```
   Write(".arena/runs/<name>/adjudication-config.yaml", <yaml_content>)
   ```

6. **Launch orchestrator:**
   ```
   Task(
     subagent_type="arena",
     prompt="Run reliable-generation for: <name>\nMax iterations: <N>",
     run_in_background=true,
     description="Generating with constraints"
   )
   ```

7. **Report to caller:**
   ```
   Started reliable generation: <name>

   Goal: <first 100 chars>...
   Constraints: <list of constraint ids>
   Adjudication: <approval policy summary, or "defaults" if not specified>
   Max iterations: <N>

   Progress: tail -f .arena/runs/<name>/live.log
   Output: .arena/runs/<name>/final/artifact.md
   ```

### Phase 4: Setup Mode (`--setup`)

Interactive setup for users who want to edit files manually.

1. Create run directory with templates from `${CLAUDE_PLUGIN_ROOT}/templates/reliable-generation/`

2. Ask what they want to generate:
   ```
   AskUserQuestion([{
     "question": "What type of content?",
     "header": "Content",
     "options": [
       {"label": "Story/Narrative", "description": "Fiction, bedtime stories"},
       {"label": "Documentation", "description": "Technical docs, guides"},
       {"label": "Marketing Copy", "description": "Ads, landing pages"},
       {"label": "Other", "description": "Custom content type"}
     ]
   }])
   ```

3. Customize templates based on response.

4. Tell user to edit files and run `--run`.

### Phase 5: Dry Run Mode (`--dry-run`)

Preview constraint routing without executing:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/arena.py \
  --config ${CLAUDE_PLUGIN_ROOT}/config/arena.config.json \
  --name <name> -p reliable-generation --dry-run
```

### Phase 6: Run Mode (`--run`)

1. Validate:
   - `.arena/runs/<name>/goal.md` exists and has content
   - `.arena/runs/<name>/constraints/` has at least one `.yaml` file

2. If missing, offer to run `--setup`.

3. Launch orchestrator (same as inline mode step 6).

## HITL Handling

The SubagentStop hook handles escalation:
- **Thrashing**: Same issues return → asks user for guidance
- **Conflicting criticals**: Constraints conflict → asks user to prioritize
- **Max iterations**: Limit reached → asks user to accept or continue
