---
description: Initialize genloop configuration interactively
argument-hint: [--template <name> | --list]
allowed-tools: Bash, Read, Write, Glob, AskUserQuestion
---

# Arena Init

Initialize a genloop configuration for your project. Creates `.arena/genloop.yaml` and `.arena/constraints/` with appropriate settings.

## Usage Modes

### Mode 1: Template (--template)

```
/arena:init --template docs
/arena:init --template code
/arena:init --template stories
```

Copies a preset bundle directly - skips the wizard.

### Mode 2: List (--list)

```
/arena:init --list
```

Shows available presets and constraint templates.

### Mode 3: Interactive (default)

```
/arena:init
```

Runs the adaptive wizard to configure settings.

## Workflow

### Phase 1: Parse Arguments

Check `$ARGUMENTS`:

- If contains `--list`: Go to **List Mode**
- If contains `--template <name>`: Go to **Template Mode**
- Otherwise: Go to **Wizard Mode**

### Phase 2: List Mode (--list)

Read and display available templates:

1. List presets from `${CLAUDE_PLUGIN_ROOT}/templates/presets/`:
   ```bash
   ls ${CLAUDE_PLUGIN_ROOT}/templates/presets/
   ```

2. List constraint templates from `${CLAUDE_PLUGIN_ROOT}/templates/constraints/`:
   ```bash
   find ${CLAUDE_PLUGIN_ROOT}/templates/constraints -name "*.yaml" -type f
   ```

3. Output formatted list:
   ```
   ## Available Presets

   | Preset | Description |
   |--------|-------------|
   | docs | Technical documentation, guides, API docs |
   | code | Code generation, implementation |
   | stories | User stories, specifications |

   ## Constraint Templates

   **Documentation:**
   - accuracy - Factual accuracy for docs
   - clarity - Readability and clarity

   **Code:**
   - correctness - Syntax and logic correctness
   - security - Security vulnerabilities
   - testability - Code testability

   **Common:**
   - completeness - Covers all requirements
   - consistency - Follows patterns

   Usage:
   - Preset: `/arena:init --template docs`
   - Wizard: `/arena:init`
   ```

### Phase 3: Template Mode (--template)

1. Extract template name from arguments
2. Verify preset exists:
   ```bash
   ls ${CLAUDE_PLUGIN_ROOT}/templates/presets/<name>/config.yaml
   ```
3. Create .arena directory:
   ```bash
   mkdir -p .arena/constraints
   ```
4. Copy preset config:
   ```bash
   cp ${CLAUDE_PLUGIN_ROOT}/templates/presets/<name>/config.yaml .arena/genloop.yaml
   ```
5. Copy preset constraints:
   ```bash
   cp ${CLAUDE_PLUGIN_ROOT}/templates/presets/<name>/constraints/*.yaml .arena/constraints/
   ```
6. Report success:
   ```
   Initialized with **<name>** preset.

   Created:
   - .arena/genloop.yaml (configuration)
   - .arena/constraints/*.yaml (constraint files)

   Next steps:
   1. Create a run: `/arena:genloop my-run --goal "..."`
   2. Or edit config: `.arena/genloop.yaml`
   ```

### Phase 4: Wizard Mode (default)

**First, display the genloop introduction:**

Output this welcome message to explain how genloop works:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   ╔═╗╔═╗╔╗╔╦  ╔═╗╔═╗╔═╗                                                     │
│   ║ ╦║╣ ║║║║  ║ ║║ ║╠═╝  Constraint-Driven Content Generation              │
│   ╚═╝╚═╝╝╚╝╩═╝╚═╝╚═╝╩                                                       │
│                                                                             │
│   Genloop ensures your generated content meets quality standards through    │
│   an iterative Generate → Critique → Refine loop with multi-agent review.  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   HOW IT WORKS                                                              │
│                                                                             │
│        ┌──────────────┐                                                     │
│        │     GOAL     │  Your objective + source material                   │
│        │ + CONSTRAINTS│  Quality rules (accuracy, security, etc.)           │
│        └──────┬───────┘                                                     │
│               │                                                             │
│               ▼                                                             │
│        ┌──────────────┐ ◄──────────────────────────────────────────────┐    │
│        │   GENERATE   │  Claude creates/refines content                │    │
│        └──────┬───────┘                                                │    │
│               │                                                        │    │
│               ▼                                                        │    │
│        ┌──────────────┐                                                │    │
│        │   CRITIQUE   │  Fan out to all constraints × all agents       │    │
│        └──────┬───────┘                                                │    │
│               │                                                        │    │
│       ┌───────┼───────┬───────┐                                        │    │
│       ▼       ▼       ▼       ▼                                        │    │
│   ┌───────┐┌───────┐┌───────┐                                          │    │
│   │Constr.││Constr.││       │                                          │    │
│   │   1   ││   2   ││  ...  │  Your selected constraints               │    │
│   │ C|X|G ││ C|X|G ││       │  Each reviewed by all 3 agents           │    │
│   └───┬───┘└───┬───┘└───┬───┘  C=Claude  X=Codex  G=Gemini             │    │
│       │        │        │                                              │    │
│       └────────┼────────┘                                              │    │
│                ▼                                                       │    │
│        ┌──────────────┐                                                │    │
│        │  ADJUDICATE  │  Merge critiques, resolve conflicts            │    │
│        └──────┬───────┘                                                │    │
│               │                                                        │    │
│          ┌────┴────┐                                                   │    │
│          ▼         ▼                                                   │    │
│      ┌───────┐ ┌───────┐                                               │    │
│      │APPROVE│ │REFINE │───────────────────────────────────────────────┘    │
│      └───┬───┘ └───────┘  Loop back through critique until approved         │
│          │                                                                  │
│          ▼                                                                  │
│        ┌──────────────┐                                                     │
│        │    OUTPUT    │  Quality-assured content in .arena/runs/final/      │
│        └──────────────┘                                                     │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   This wizard will help you configure:                                      │
│   • What constraints to apply (accuracy, security, clarity, etc.)           │
│   • How strict approval should be                                           │
│   • Which agents participate in critique                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Then run the adaptive wizard using AskUserQuestion.**

#### Screen 1: Essential Questions

```
AskUserQuestion([
  {
    "question": "What are you generating?",
    "header": "Content",
    "options": [
      {"label": "Documentation", "description": "Technical docs, guides, API references"},
      {"label": "Code", "description": "Implementation, scripts, functions"},
      {"label": "User Stories", "description": "Specs, requirements, acceptance criteria"},
      {"label": "Other", "description": "Custom content type"}
    ],
    "multiSelect": false
  }
])
```

Based on content type, ask about quality aspects:

**If Documentation:**
```
AskUserQuestion([
  {
    "question": "What quality aspects matter most?",
    "header": "Quality",
    "options": [
      {"label": "Accuracy (Recommended)", "description": "Facts must be correct and verifiable"},
      {"label": "Clarity", "description": "Easy to understand, well-structured"},
      {"label": "Completeness", "description": "Covers all requirements thoroughly"},
      {"label": "Consistency", "description": "Follows established patterns"}
    ],
    "multiSelect": true
  }
])
```

**If Code:**
```
AskUserQuestion([
  {
    "question": "What quality aspects matter most?",
    "header": "Quality",
    "options": [
      {"label": "Correctness (Recommended)", "description": "Code compiles and runs correctly"},
      {"label": "Security", "description": "No vulnerabilities, OWASP compliant"},
      {"label": "Testability", "description": "Code can be effectively tested"},
      {"label": "Consistency", "description": "Follows codebase patterns"}
    ],
    "multiSelect": true
  }
])
```

**If User Stories:**
```
AskUserQuestion([
  {
    "question": "What quality aspects matter most?",
    "header": "Quality",
    "options": [
      {"label": "Acceptance Criteria (Recommended)", "description": "Clear, testable criteria"},
      {"label": "Scope", "description": "Appropriately sized for iteration"},
      {"label": "Testability", "description": "Can be verified effectively"},
      {"label": "Completeness", "description": "Covers edge cases"}
    ],
    "multiSelect": true
  }
])
```

**If Other:**
```
AskUserQuestion([
  {
    "question": "What quality aspects matter most?",
    "header": "Quality",
    "options": [
      {"label": "Accuracy", "description": "Facts must be correct"},
      {"label": "Clarity", "description": "Easy to understand"},
      {"label": "Completeness", "description": "Covers all requirements"},
      {"label": "Consistency", "description": "Follows patterns"}
    ],
    "multiSelect": true
  }
])
```

#### Screen 2: Advanced Options Prompt

```
AskUserQuestion([
  {
    "question": "Configure advanced options?",
    "header": "Advanced",
    "options": [
      {"label": "No, use defaults (Recommended)", "description": "Balanced strictness, surgical edits, all agents"},
      {"label": "Yes, customize", "description": "Configure approval strictness, refinement style, agents"}
    ],
    "multiSelect": false
  }
])
```

#### Screen 3: Advanced Options (if yes)

Only show if user selected "Yes, customize":

```
AskUserQuestion([
  {
    "question": "How strict should approval be?",
    "header": "Strictness",
    "options": [
      {"label": "Balanced (Recommended)", "description": "Block on CRITICAL and HIGH issues"},
      {"label": "Strict", "description": "Block on any unresolved issue"},
      {"label": "Lenient", "description": "Block on CRITICAL only"}
    ],
    "multiSelect": false
  },
  {
    "question": "How should refinements work?",
    "header": "Refinement",
    "options": [
      {"label": "Surgical edits (Recommended)", "description": "Small targeted fixes to existing content"},
      {"label": "Full rewrites", "description": "Regenerate addressing all feedback"}
    ],
    "multiSelect": false
  },
  {
    "question": "Which agents should critique?",
    "header": "Agents",
    "options": [
      {"label": "All three (Recommended)", "description": "Claude, Codex, and Gemini for diverse perspectives"},
      {"label": "Claude + Codex", "description": "Faster, still diverse"},
      {"label": "Claude only", "description": "Fastest, single perspective"}
    ],
    "multiSelect": false
  }
])
```

### Phase 5: Generate Configuration

Based on wizard answers, generate `.arena/genloop.yaml`:

#### Mapping Answers to Config

| Content Type | Default Preset Base |
|--------------|---------------------|
| Documentation | docs |
| Code | code |
| User Stories | stories |
| Other | docs (modified) |

| Quality Aspect | Constraint File |
|----------------|-----------------|
| Accuracy | constraints/docs/accuracy.yaml |
| Clarity | constraints/docs/clarity.yaml |
| Correctness | constraints/code/correctness.yaml |
| Security | constraints/code/security.yaml |
| Testability | constraints/code/testability.yaml OR constraints/stories/testability.yaml |
| Acceptance Criteria | constraints/stories/acceptance-criteria.yaml |
| Scope | constraints/stories/scope.yaml |
| Completeness | constraints/common/completeness.yaml |
| Consistency | constraints/common/consistency.yaml |

| Strictness | approve_when |
|------------|--------------|
| Strict | all_resolved |
| Balanced | no_critical_and_no_high |
| Lenient | no_critical |

| Refinement | mode |
|------------|------|
| Surgical edits | edit |
| Full rewrites | rewrite |

| Agents | default_agents |
|--------|----------------|
| All three | ["claude", "codex", "gemini"] |
| Claude + Codex | ["claude", "codex"] |
| Claude only | ["claude"] |

#### Generate genloop.yaml

Create `.arena/genloop.yaml` with structure:

```yaml
# Generated by /arena:init
# Content type: <content_type>

max_iterations: 5

constraints:
  dir: .arena/constraints
  routing:
    default_agents: <agents>

phases:
  generate:
    agent: claude
  critique:
    pattern: parallel
  adjudicate:
    agent: claude
  refine:
    agent: claude
    mode: <refinement_mode>
    validation_retries: 2

adjudication:
  approval:
    block_on: <block_on_severities>
  escalation:
    triggers: ["max_iterations", "thrashing", "conflicting_criticals"]

termination:
  approve_when: <approve_when>
  escalate_on: ["max_iterations", "thrashing"]

output:
  dir: final
  filename: artifact.md
```

### Phase 6: Copy Constraint Files

1. Create constraints directory:
   ```bash
   mkdir -p .arena/constraints
   ```

2. Copy selected constraint files from templates:
   ```bash
   cp ${CLAUDE_PLUGIN_ROOT}/templates/constraints/<category>/<name>.yaml .arena/constraints/
   ```

### Phase 7: Report Success

Output summary:

```
## Genloop Initialized

**Content type:** <type>
**Constraints:** <list>
**Approval policy:** <policy>
**Refinement mode:** <mode>
**Agents:** <agents>

Created:
- `.arena/genloop.yaml` - Configuration
- `.arena/constraints/` - Constraint files

**Next steps:**
1. Create a run:
   ```
   /arena:genloop my-run --goal "Generate documentation for the API"
   ```

2. Or review/edit the config:
   ```
   .arena/genloop.yaml
   .arena/constraints/*.yaml
   ```

**Tip:** Run `/arena:genloop --help` for full command reference.
```

## Default Values (when advanced options skipped)

| Setting | Default |
|---------|---------|
| Strictness | Balanced (no_critical_and_no_high) |
| Refinement | Surgical edits (edit) |
| Agents | All three (claude, codex, gemini) |
| Max iterations | 5 |
