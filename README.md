# Agent Arena

Multi-agent orchestration plugins for Claude Code.

## Installation

### Add as Marketplace

```bash
# Add the marketplace
claude plugin marketplace add mikewrather/agent-arena

# Install the plugin
claude plugin install arena@agent-arena --scope user
```

### Direct Installation (if marketplace is already added)

```bash
claude plugin install arena@agent-arena
```

## Plugins

### arena

Multi-agent orchestration system that coordinates Claude Code, Codex CLI, and Gemini CLI for:

- **Code Review**: Get 3 independent perspectives on code changes
- **Security Audits**: Adversarial analysis to find vulnerabilities
- **Architecture Decisions**: Debate trade-offs from multiple angles
- **Brainstorming**: Collaborative idea generation
- **Reliable Generation**: Constraint-driven content generation with iterative refinement

[View Plugin Documentation](./plugins/arena/README.md)

## Requirements

- Claude Code CLI
- Codex CLI (`codex`)
- Gemini CLI (`gemini`)
- Python 3.10+

## License

MIT
