# Arena

A Claude Code plugin for multi-agent orchestration that coordinates Claude Code, Codex CLI, and Gemini CLI for collaborative analysis.

## Installation

```bash
# Add marketplace (one-time)
claude plugin marketplace add mikewrather/agent-arena

# Install plugin
claude plugin install arena@agent-arena --scope user
```

## Features

- **Multi-Agent Orchestration**: Coordinate 3 AI models (Claude, Codex, Gemini) working together
- **Multiple Profiles**: Security audits, code reviews, brainstorming, deep analysis
- **Reliable Generation**: Constraint-driven content generation with iterative refinement
- **Human-in-the-Loop**: Automatic escalation when agent consensus fails

## Quick Start

### Code Review
```bash
/arena:run Review the authentication module -p security-audit
```

### Reliable Generation
```bash
/arena:genloop story \
  --goal "Generate a bedtime story for ages 4-6 about sharing" \
  --constraint "safety: No unresolved fear, age-appropriate" \
  --constraint "quality: Complete narrative arc"
```

### Check Status
```bash
/arena:status
```

## Commands

| Command | Description |
|---------|-------------|
| `/arena:run` | Run orchestration with any profile |
| `/arena:genloop` | Constraint-driven content generation |
| `/arena:status` | Check current run status |
| `/arena:resume` | Resume after human input needed |
| `/arena:profiles` | List available profiles |

## Profiles

| Profile | Mode | Best For |
|---------|------|----------|
| `security-audit` | Adversarial | Finding vulnerabilities |
| `code-review` | Collaborative | Standard code quality |
| `brainstorm` | Collaborative | Creative ideation |
| `opus-deep` | Adversarial | Deep analysis (3x Opus) |
| `multi-expert` | Collaborative | Architecture decisions |
| `reliable-generation` | Multi-phase | Constraint-driven generation |

## Requirements

- Claude Code CLI (`claude`)
- Codex CLI (`codex`)
- Gemini CLI (`gemini`)
- Python 3.10+
- [UV](https://docs.astral.sh/uv/) (dependencies installed automatically on first run)

## Directory Structure

```
arena/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── agents/
│   └── arena.md             # Subagent definition
├── commands/
│   ├── run.md               # /arena:run
│   ├── genloop.md           # /arena:genloop
│   ├── status.md            # /arena:status
│   ├── resume.md            # /arena:resume
│   └── profiles.md          # /arena:profiles
├── skills/
│   └── arena/
│       └── SKILL.md         # Auto-discovered skill
├── config/
│   ├── profiles/            # Profile configurations
│   ├── personas/            # Agent personas
│   ├── modes/               # Interaction modes
│   └── schemas/             # JSON schemas
├── scripts/
│   └── arena.py             # Main orchestrator
└── templates/
    └── reliable-generation/ # Constraint templates
```

## License

MIT
