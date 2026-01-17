# Profiles Reference

Profiles bundle configuration presets for common use cases.

## Built-in Profiles

### security-audit
```json
{
  "mode": "adversarial",
  "pattern": "parallel",
  "turns": 2,
  "personas": {
    "claude": "security-auditor",
    "codex": "security-auditor",
    "gemini": "security-auditor"
  },
  "stop_on_consensus": true
}
```

### code-review
```json
{
  "mode": "collaborative",
  "pattern": "sequential",
  "turns": 3,
  "personas": {
    "claude": "code-reviewer",
    "codex": "code-reviewer",
    "gemini": "code-reviewer"
  }
}
```

### brainstorm
```json
{
  "mode": "collaborative",
  "pattern": "parallel",
  "turns": 4,
  "personas": {
    "claude": "architect",
    "codex": "ux-designer",
    "gemini": "performance-engineer"
  }
}
```

### opus-deep
```json
{
  "mode": "collaborative",
  "pattern": "sequential",
  "turns": 6,
  "agents": {
    "claude": {"cmd": ["claude", "-p", "--model", "opus", "--max-turns", "1"]},
    "codex": {"cmd": ["claude", "-p", "--model", "opus", "--max-turns", "1"]},
    "gemini": {"cmd": ["claude", "-p", "--model", "opus", "--max-turns", "1"]}
  }
}
```

### multi-expert (Dynamic Routing)
```json
{
  "mode": "collaborative",
  "pattern": "sequential",
  "turns": 6,
  "stop_on_consensus": true,
  "routing": true
}
```

When `routing: true`, the system automatically selects the best 3 experts from a pool of 13 specialists based on your goal. The router analyzes your goal and picks complementary experts.

**Available experts**: agent-architect, architect, langchain-expert, python-expert, backend-engineer, security-auditor, performance-engineer, testing-engineer, devops-engineer, ml-engineer, data-engineer, code-reviewer, ux-designer

### static-expert (Fixed Panel)
```json
{
  "mode": "collaborative",
  "pattern": "sequential",
  "turns": 6,
  "personas": {
    "claude": "architect",
    "codex": "security-auditor",
    "gemini": "performance-engineer"
  }
}
```

Use this for a fixed architect + security + performance panel without dynamic routing.

### research-brainstorm
```json
{
  "mode": "collaborative",
  "pattern": "sequential",
  "turns": 12,
  "enable_research": true,
  "research_agent": "gemini"
}
```

## Custom Profiles

Create in `.arena/profiles/<name>.json`. Project profiles override plugin profiles.
