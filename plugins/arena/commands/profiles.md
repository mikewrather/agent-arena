---
description: List available arena profiles
allowed-tools: Bash, Read
---

# Arena Profiles

List all available orchestration profiles.

## Usage

```
/arena:profiles
```

## Workflow

List profiles from the plugin's config directory:

```bash
for f in ${CLAUDE_PLUGIN_ROOT}/config/profiles/*.json; do
  name=$(basename "$f" .json)
  desc=$(python3 -c "import json; print(json.load(open('$f')).get('description', 'No description'))")
  echo "- $name: $desc"
done
```

## Built-in Profiles

| Profile | Mode | Pattern | Turns | Description |
|---------|------|---------|-------|-------------|
| `security-audit` | adversarial | parallel | 2 | Security-focused review with security-auditor personas |
| `code-review` | collaborative | sequential | 3 | Standard code review with code-reviewer personas |
| `brainstorm` | collaborative | parallel | 4 | Creative exploration with diverse expert personas |
| `opus-deep` | collaborative | sequential | 6 | Deep analysis using 3 Claude Opus 4.5 instances |
| `multi-expert` | collaborative | sequential | 6 | **Dynamic routing**: auto-selects best experts for goal |
| `static-expert` | collaborative | sequential | 6 | Fixed panel: architect + security + performance |
| `research-brainstorm` | collaborative | sequential | 12 | Brainstorm with web research capability |
| `reliable-generation` | - | multi-phase | 3 iter | **Constraint-driven generation** with critique loop |

### Reliable Generation

The `reliable-generation` profile uses a different pattern:
- **Generate** → **Parallel Critique** → **Adjudicate** → **Refine** loop
- Requires constraint files in `constraints/` directory
- Use `/arena:genloop` for setup workflow

## Custom Profiles

Create project-specific profiles in `.arena/profiles/<name>.json`:

```json
{
  "description": "My custom profile",
  "mode": "adversarial",
  "pattern": "parallel",
  "turns": 3,
  "personas": {
    "claude": "my-persona",
    "codex": "my-persona",
    "gemini": "my-persona"
  }
}
```

Project profiles override plugin profiles with the same name.
