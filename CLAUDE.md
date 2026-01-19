# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Arena is a file-driven multi-agent orchestration system that coordinates Claude Code CLI, Codex CLI, and Gemini CLI as external workers. The system uses filesystem state as the source of truth, enabling deterministic, auditable, and replayable agent conversations.

## Installation

Arena is distributed as a Claude Code plugin:

```bash
# Install the plugin (user scope)
claude plugin install arena@agent-arena

# Verify installation
claude plugin list
```

Plugin files are installed to `~/.claude/plugins/` via the agent-arena marketplace.

## Quick Start

Use the `/arena` slash commands:

```bash
# Run multi-agent orchestration
/arena:run --name auth-review -p security-audit

# Constraint-driven generation (genloop)
/arena:genloop --name story-gen -p reliable-generation

# Check status of runs
/arena:status

# Resume after HITL interruption
/arena:resume --name auth-review

# List available profiles
/arena:profiles
```

Or invoke programmatically:

```bash
# Create a named run (creates template goal.yaml)
python3 ~/.claude/plugins/marketplaces/agent-arena/plugins/arena/scripts/arena.py \
  --config ~/.claude/plugins/marketplaces/agent-arena/plugins/arena/config/arena.config.json \
  --name auth-review

# Edit the goal file
vim .arena/runs/auth-review/goal.yaml

# Run with a profile
python3 ~/.claude/plugins/marketplaces/agent-arena/plugins/arena/scripts/arena.py \
  --name auth-review -p security-audit

# Watch progress (another terminal)
tail -f .arena/live.log
```

## Profiles

Profiles bundle configuration into reusable presets:

| Profile | Description |
|---------|-------------|
| `security-audit` | Adversarial parallel with security-auditor personas |
| `code-review` | Standard sequential code review |
| `brainstorm` | Collaborative parallel with diverse experts |
| `opus-deep` | Deep analysis with 3 Claude Opus 4.5 instances |
| `multi-expert` | Architect + security + performance panel |
| `research-brainstorm` | Sequential brainstorm with web research |
| `reliable-generation` | Constraint-driven generation with critique loop |

See `arena-plugin/plugins/arena/config/profiles/README.md` for flow diagrams and detailed documentation.

## Architecture

### Core Concepts

- **File-based message bus**: All state lives in `.arena/` directory - no nested subagent spawning
- **External CLI workers**: Agents are external processes (claude, codex, gemini CLIs), not embedded subagents
- **Strict JSON envelope**: Every agent returns `{status, message, questions, artifacts, confidence, agrees_with, objections}`
- **HITL interrupts**: `status: "needs_human"` stops orchestration immediately (exit code 10)
- **Atomic file operations**: All writes use temp file + rename for crash safety
- **Process timeouts**: Configurable per-agent timeout (default 300s) prevents hung processes

### Conversation Patterns

1. **Sequential (turn-taking)**: Claude → Codex → Gemini → Claude... (for dialogue/refinement)
2. **Parallel (analyze/compare)**: All agents respond independently, then moderator synthesizes
3. **Multi-phase (reliable generation)**: Generate → Critique → Adjudicate → Refine loop

### Reliable Generation Pattern

The `reliable-generation` profile implements constraint-driven generation:

```
Goal + Constraints → [Generate] → [Parallel Critique] → [Adjudicate] → Approved?
                          ↑                                      ↓ No
                          └──────────── [Refine] ←───────────────┘
```

**Key features:**
- **Constraint files**: YAML files in `constraints/` define rules and severity levels
- **All-to-all critique**: Every constraint reviewed by all 3 agents
- **Adjudicator**: Claude Opus 4.5 resolves conflicting feedback and balances constraints
- **Iteration loop**: Refinement continues until approved or max iterations reached
- **HITL escalation**: Thrashing or conflicting criticals trigger human intervention

**Usage:**
```bash
# Create run with reliable-generation profile
/arena:genloop --name story-gen -p reliable-generation

# Edit goal.yaml and add constraints to .arena/runs/story-gen/constraints/
# Run with dry-run to preview routing
/arena:genloop --name story-gen -p reliable-generation --dry-run

# Execute
/arena:genloop --name story-gen -p reliable-generation
```

### Mode vs Persona Separation

- **Mode**: Interaction policy (adversarial, collaborative) - controls how agents behave with each other
- **Persona**: Expert lens (code-reviewer, security-auditor) - defines what each agent focuses on
- **Pattern**: Orchestration topology (sequential/parallel) - can be mode default or runtime override

## Directory Structure

```
project/
├── .arena/                          # Local arena state (per-project)
│   ├── live.log                     # Symlink to current run's live.log
│   ├── modes/                       # Optional local mode overrides
│   ├── personas/                    # Optional local persona overrides
│   ├── profiles/                    # Optional local profile overrides
│   └── runs/                        # Named run folders
│       ├── latest -> auth-review    # Symlink to most recent run
│       ├── auth-review/
│       │   ├── goal.yaml            # Goal + source for this run
│       │   ├── context.md           # Optional additional context
│       │   ├── live.log             # Streaming output
│       │   ├── state.json           # Run state
│       │   ├── thread.jsonl         # Full conversation
│       │   ├── resolution.json      # How run ended
│       │   ├── hitl/                # HITL questions/answers
│       │   ├── constraints/         # Constraint YAML files (genloop)
│       │   └── turns/
│       │       └── turn_0001/
│       │           ├── prompt_*.txt
│       │           └── out_*.json
│       └── story-gen/
│           └── ...
```

## Goal File Format

Arena uses `goal.yaml` (preferred) or `goal.md` (legacy) for defining objectives:

```yaml
# goal.yaml
goal: |
  Describe your objective here.

  ## Focus Areas
  1. Primary focus
  2. Secondary focus

# Optional: Source material for agents
source:
  files:
    - "{{project_root}}/path/to/file.md"
  globs:
    - "{{project_root}}/docs/*.md"
  scripts:
    - git log --oneline -20
  inline: |
    Additional context here.
```

### Path Variables

| Variable | Description |
|----------|-------------|
| `{{project_root}}` | Project root (directory containing `.arena/`) |
| `{{run_dir}}` | Run directory (`.arena/runs/<name>/`) |
| `{{constraint_dir}}` | Directory containing the constraint YAML file |
| `{{arena_home}}` | Global arena home (`~/.arena/`) - read-only |
| `{{artifact}}` | Current artifact path (constraints only, during critique) |
| `{{source}}` | Path to source.md (legacy) |

## CLI Invocation Patterns

```bash
# Claude Code: print mode, single turn (streaming enabled)
claude -p --max-turns 1

# Codex: exec mode with stdin prompt, output to stdout
codex exec --sandbox read-only -o - -

# Gemini: default mode (streaming enabled)
gemini
```

All agents receive prompts via stdin with instructions to output JSON. The orchestrator parses JSON from the response text (extracting from markdown code blocks if needed).

## Termination Policies

1. **all_done**: All agents report `status: "done"` in a full cycle (sequential) or single round (parallel)
2. **consensus**: 2-of-3 agents agree (via `agrees_with` field or message similarity >85%)
3. **stagnation**: Last 2 rounds show >90% message similarity (no progress)
4. **max_turns**: Safety cap (exit code 1 vs 0 for natural resolution)

## HITL Protocol

When an agent needs human input:
1. Orchestrator writes `.arena/runs/<name>/hitl/questions.json` with structured questions
2. Sets `state.json` → `awaiting_human: true`
3. Exits with code 10

To resume:
1. Read questions from `.arena/runs/<name>/hitl/questions.json`
2. Create `.arena/runs/<name>/hitl/answers.json`:
   ```json
   {"answers": [{"question_id": "q1", "answer": "your answer"}]}
   ```
3. Run `/arena:resume --name <name>`

## Development

```bash
# Run tests
cd arena-plugin && uvx --with pyyaml pytest tests/ -v

# Validate plugin manifest
claude plugin validate arena-plugin/plugins/arena
```

## Plugin Structure

```
arena-plugin/
├── plugins/arena/
│   ├── .claude-plugin/
│   │   └── plugin.json              # Plugin manifest
│   ├── agents/
│   │   └── arena.md                 # Subagent definition
│   ├── commands/
│   │   ├── run.md                   # /arena:run command
│   │   ├── genloop.md               # /arena:genloop command
│   │   ├── status.md                # /arena:status command
│   │   ├── resume.md                # /arena:resume command
│   │   └── profiles.md              # /arena:profiles command
│   ├── config/
│   │   ├── arena.config.json        # Default agent configuration
│   │   ├── modes/                   # Mode definitions
│   │   ├── personas/                # Persona definitions
│   │   └── profiles/                # Profile presets
│   ├── hooks/
│   │   └── hooks.json               # Hook definitions
│   ├── scripts/
│   │   ├── arena.py                 # Main orchestrator
│   │   ├── models.py                # Data models
│   │   ├── sources.py               # Source resolution
│   │   └── utils.py                 # Utilities
│   ├── skills/
│   │   └── arena/                   # Skill definition
│   └── templates/
│       └── reliable-generation/     # Template files
└── tests/
    └── test_triad.py                # Test suite
```
