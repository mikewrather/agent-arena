# Personas Reference

Personas define what each agent focuses on and their expertise.

## Built-in Personas

| Persona | Focus |
|---------|-------|
| `code-reviewer` | Code quality, correctness, maintainability |
| `security-auditor` | Vulnerabilities, attack vectors, security best practices |
| `architect` | System design, patterns, scalability, trade-offs |
| `performance-engineer` | Efficiency, optimization, resource usage |
| `ux-designer` | User experience, accessibility, usability |

## Persona Assignment

Profiles assign personas to agents:

```json
{
  "personas": {
    "claude": "security-auditor",
    "codex": "code-reviewer",
    "gemini": "architect"
  }
}
```

## Custom Personas

Create project-specific personas in `.arena/personas/<name>.md`:

```markdown
---
name: my-persona
---

# My Custom Persona

You are an expert in [domain]. Focus on:
- [Area 1]
- [Area 2]
- [Area 3]

When reviewing, pay special attention to...
```

Project personas override plugin personas with the same name.
