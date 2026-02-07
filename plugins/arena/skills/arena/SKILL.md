---
name: arena
description: Multi-agent orchestration coordinating Claude Code, Codex, and Gemini CLIs. Use for code review, security audits, architecture decisions, or any task needing multiple AI perspectives.
---

# Arena

Coordinate three AI agents (Claude, Codex, Gemini) for collaborative or adversarial analysis.

## Quick Start

```bash
# Run with a profile
uv run --project ${CLAUDE_PLUGIN_ROOT} python3 ${CLAUDE_PLUGIN_ROOT}/scripts/arena.py \
  --config ${CLAUDE_PLUGIN_ROOT}/config/arena.config.json \
  --name my-review -p code-review

# Watch progress
tail -f .arena/runs/my-review/live.log
```

## When to Use

- **Code review**: Get 3 independent perspectives on changes
- **Security audit**: Adversarial analysis to find vulnerabilities
- **Architecture decisions**: Debate trade-offs from multiple angles
- **Brainstorming**: Collaborative idea generation
- **Content generation**: Constraint-driven generation with quality assurance

## Profiles

| Profile | Best For |
|---------|----------|
| `security-audit` | Finding vulnerabilities, adversarial review |
| `code-review` | Standard code quality review |
| `brainstorm` | Creative exploration, ideation |
| `opus-deep` | Deep analysis needing extended reasoning |
| `multi-expert` | Architecture with multiple specialists |
| `research-brainstorm` | Ideas requiring web research |
| `reliable-generation` | Constraint-driven content generation |

## Commands

| Command | Usage |
|---------|-------|
| `/arena:run` | Run any profile in background |
| `/arena:genloop` | Reliable generation with setup workflow |
| `/arena:status` | Check run status |
| `/arena:resume` | Resume after HITL |
| `/arena:profiles` | List available profiles |

## Output Location

All run artifacts are in `.arena/runs/<run-name>/`:
- `goal.yaml` - The objective (or legacy `goal.md`)
- `thread.jsonl` - Full conversation
- `resolution.json` - Why it stopped
- `live.log` - Real-time streaming output

## Reference

For detailed configuration options, see:
- [modes.md](reference/modes.md) - Adversarial vs collaborative behavior
- [personas.md](reference/personas.md) - Agent specializations
- [profiles.md](reference/profiles.md) - Profile definitions
