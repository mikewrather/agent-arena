# Plan: Interactive `/arena:init` Wizard

## Goal
Create an `/arena:init` command that uses `AskUserQuestion` to guide users through setting up genloop configs with questions about constraints, adjudication, and nuance settings.

## Question Flow Design

### Screen 1: Content & Constraints
```
Q1: What are you generating?
    ○ Documentation / Technical writing
    ○ Code / Implementation
    ○ User stories / Specs
    ○ Other (describe)

Q2: What quality aspects matter most? (multi-select)
    ☐ Accuracy - Facts must be correct
    ☐ Clarity - Easy to understand
    ☐ Completeness - Cover all requirements
    ☐ Security - No vulnerabilities
```

### Screen 2: Adjudication & Nuance
```
Q3: How strict should approval be?
    ○ Strict - Block on any HIGH or CRITICAL issue
    ○ Balanced - Block on CRITICAL only (Recommended)
    ○ Lenient - Advisory only, rarely block

Q4: When constraints conflict, how should they be resolved?
    ○ Prioritize accuracy over brevity
    ○ Prioritize completeness over conciseness
    ○ Balance equally (adjudicator decides)
    ○ I'll configure tensions manually
```

### Screen 3: Iteration & Refinement
```
Q5: How should refinements work?
    ○ Surgical edits - Small targeted fixes (Recommended)
    ○ Full rewrites - Regenerate addressing all feedback

Q6: How many refinement iterations before escalating to human?
    ○ 3 iterations (quick feedback loop)
    ○ 5 iterations (balanced)
    ○ 10 iterations (thorough, let it work)
```

### Screen 4: Agent Configuration
```
Q7: Which agents should critique?
    ○ All three (Claude, Codex, Gemini) (Recommended)
    ○ Claude + Codex (faster, still diverse)
    ○ Claude only (fastest, single perspective)
```

## Files to Create/Modify

### 1. New Command: `plugins/arena/commands/init.md`
- Frontmatter with allowed-tools including `AskUserQuestion`
- Multi-phase workflow using question responses
- Generates config and constraints based on answers

### 2. Constraint Templates Library: `plugins/arena/templates/constraints/`
```
templates/constraints/
├── accuracy.yaml      # Facts must be verifiable
├── clarity.yaml       # Easy to understand
├── completeness.yaml  # Covers all requirements
├── security.yaml      # No vulnerabilities
├── brevity.yaml       # Concise, no fluff
├── correctness.yaml   # Code compiles/runs
├── testability.yaml   # Can be tested
├── consistency.yaml   # Follows patterns
└── README.md          # Template documentation
```

### 3. Config Generation Logic
The command will assemble a `genloop.yaml` based on answers:

```yaml
# Generated based on user selections
max_iterations: {from Q6}

constraints:
  dir: .arena/constraints
  routing:
    default_agents: {from Q7}

phases:
  refine:
    mode: {from Q5: "edit" or "rewrite"}

adjudication:
  approval:
    policy: {from Q3: "all_resolved", "no_critical_and_no_high", "no_critical"}
  tensions:
    {from Q4: generate tension axes}

termination:
  approve_when: {from Q3}
  escalate_on: [max_iterations, thrashing]
```

## Implementation Steps

### Step 1: Create template directory structure
```
templates/
├── presets/docs/        # --template docs bundle
├── presets/code/        # --template code bundle
├── presets/stories/     # --template stories bundle
├── constraints/docs/    # Doc-specific constraints
├── constraints/code/    # Code-specific constraints
└── constraints/common/  # Shared constraints
```

### Step 2: Create content-aware constraint files
- `constraints/docs/accuracy.yaml` - doc-focused accuracy rules
- `constraints/docs/clarity.yaml` - readability for documentation
- `constraints/code/correctness.yaml` - code compiles/runs
- `constraints/code/security.yaml` - OWASP-style security checks
- `constraints/common/completeness.yaml` - covers requirements (generic)

### Step 3: Create preset bundles
Each preset in `presets/<name>/` contains:
- `config.yaml` - pre-configured genloop settings
- `constraints/` - curated constraint set for that use case

### Step 4: Create the `init.md` command
```markdown
---
description: Initialize genloop configuration interactively
allowed-tools: AskUserQuestion, Read, Write, Bash, Glob
---

# Arena Init

## Usage Modes

### Mode 1: Preset (--template)
/arena:init --template docs
→ Copy preset bundle, skip wizard

### Mode 2: Interactive (default)
/arena:init
→ Run adaptive wizard

### Mode 3: List (--list)
/arena:init --list
→ Show available presets and constraints

## Adaptive Wizard Workflow

### Phase 1: Essential Questions
1. Content type (docs/code/stories/other)
2. Quality aspects (multi-select, options based on content type)

### Phase 2: Advanced Options Prompt
3. "Configure advanced options?" (yes/no)

### Phase 3: Advanced Questions (if yes)
4. Approval strictness
5. Refinement style
6. Agent selection

### Phase 4: Generate Files
- Create .arena/genloop.yaml
- Copy selected constraints to .arena/constraints/
- Print summary with next steps
```

### Step 5: Update plugin version
Bump to 1.8.0 for new init command.

## Constraint Template Examples

### `accuracy.yaml`
```yaml
id: accuracy
priority: 2
summary: |
  All factual claims must be accurate and verifiable.
  Do not make up information or hallucinate details.

rules:
  - id: verifiable-claims
    text: "Every factual claim must be verifiable from provided sources or common knowledge."
    default_severity: CRITICAL
    examples:
      violation: "The API was released in 2019" (when no source confirms this)
      compliant: "According to the changelog, the API was released in v2.0"

  - id: no-hallucination
    text: "Do not invent details, statistics, or quotes not present in source material."
    default_severity: CRITICAL
```

### `clarity.yaml`
```yaml
id: clarity
priority: 3
summary: |
  Content must be easy to understand for the target audience.
  Use clear language and logical structure.

rules:
  - id: plain-language
    text: "Use plain language appropriate for the audience. Avoid unnecessary jargon."
    default_severity: HIGH
    examples:
      violation: "Utilize the endpoint to instantiate a new resource"
      compliant: "Use this endpoint to create a new resource"

  - id: logical-flow
    text: "Information should flow logically from one point to the next."
    default_severity: MEDIUM
```

## Mapping Answers to Config

| Question | Answer | Config Setting |
|----------|--------|----------------|
| Q3: Strictness | Strict | `approve_when: all_resolved` |
| Q3: Strictness | Balanced | `approve_when: no_critical_and_no_high` |
| Q3: Strictness | Lenient | `approve_when: no_critical` |
| Q5: Refinement | Surgical | `phases.refine.mode: edit` |
| Q5: Refinement | Full rewrite | `phases.refine.mode: rewrite` |
| Q6: Iterations | 3/5/10 | `max_iterations: N` |
| Q7: Agents | All three | `default_agents: [claude, codex, gemini]` |
| Q7: Agents | Claude+Codex | `default_agents: [claude, codex]` |
| Q7: Agents | Claude only | `default_agents: [claude]` |

## Generated Output Structure

After running `/arena:init`, the project will have:
```
.arena/
├── genloop.yaml           # Generated config
└── constraints/           # Selected constraint files
    ├── accuracy.yaml
    ├── clarity.yaml
    └── completeness.yaml
```

## Verification

1. Run `/arena:init` and answer questions
2. Verify `.arena/genloop.yaml` contains correct settings
3. Verify `.arena/constraints/` has selected constraint files
4. Run `/arena:genloop test-run` to validate the config works
5. Check that `--list` flag shows available templates

## Design Decisions

1. **No --quick flag** - Always run the wizard to ensure users understand their config
2. **Content-aware templates** - Different constraint rules/examples for docs vs code vs stories
3. **Add --template flag** - Allow `/arena:init --template docs` to skip wizard with preset bundle
4. **Adaptive wizard** - Start with essential questions, offer "Advanced options?" to continue

## Revised Question Flow (Adaptive)

### Essential Screen (Always shown)
```
Q1: What are you generating?
    ○ Documentation / Technical writing
    ○ Code / Implementation
    ○ User stories / Specs
    ○ Other

Q2: What quality aspects matter most? (multi-select)
    [Options vary by content type from Q1]
```

### Adaptive Prompt
```
Q3: Want to configure advanced options?
    ○ Yes, customize adjudication & agents
    ○ No, use recommended defaults (Recommended)
```

### Advanced Screen (Only if Q3 = Yes)
```
Q4: How strict should approval be?
    ○ Strict / Balanced / Lenient

Q5: Refinement style?
    ○ Surgical edits / Full rewrites

Q6: Which agents should critique?
    ○ All three / Claude+Codex / Claude only
```

## Template Presets (--template flag)

| Template | Constraints Included | Default Settings |
|----------|---------------------|------------------|
| `docs` | accuracy, clarity, completeness | balanced, edit mode, all agents |
| `code` | correctness, security, testability | strict, edit mode, all agents |
| `stories` | acceptance-criteria, scope, testability | balanced, edit mode, claude+codex |
| `api` | consistency, security, documentation | strict, edit mode, all agents |

Usage: `/arena:init --template docs`

## Content-Aware Constraint Variants

### For Documentation
```yaml
# clarity-docs.yaml
rules:
  - id: plain-language
    text: "Use plain language. Avoid jargon unless defining it."
    examples:
      violation: "Utilize the endpoint to instantiate..."
      compliant: "Use this endpoint to create..."
```

### For Code
```yaml
# clarity-code.yaml
rules:
  - id: readable-code
    text: "Code should be self-documenting with clear names."
    examples:
      violation: "def f(x, y): return x + y"
      compliant: "def calculate_total(price, tax): return price + tax"
```

## Template Directory Structure

```
templates/
├── presets/              # Preset bundles for --template flag
│   ├── docs/
│   │   ├── config.yaml
│   │   └── constraints/
│   │       ├── accuracy.yaml
│   │       ├── clarity.yaml
│   │       └── completeness.yaml
│   ├── code/
│   │   └── ...
│   └── stories/
│       └── ...
└── constraints/          # Individual constraint templates
    ├── docs/             # Documentation-specific
    │   ├── accuracy.yaml
    │   └── clarity.yaml
    ├── code/             # Code-specific
    │   ├── correctness.yaml
    │   └── security.yaml
    └── common/           # Shared across types
        └── completeness.yaml
```
