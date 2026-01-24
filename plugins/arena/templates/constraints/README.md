# Constraint Templates

Pre-built constraint templates for common use cases.

## Directory Structure

```
constraints/
├── docs/           # Documentation-specific constraints
│   ├── accuracy.yaml    # Factual accuracy for docs
│   └── clarity.yaml     # Readability and clarity
├── code/           # Code-specific constraints
│   ├── correctness.yaml # Syntax and logic correctness
│   ├── security.yaml    # Security vulnerabilities
│   └── testability.yaml # Code testability
└── common/         # Shared constraints
    ├── completeness.yaml # Covers all requirements
    └── consistency.yaml  # Follows patterns
```

## Usage

Copy constraints to your project's `.arena/constraints/` directory:

```bash
# Copy specific constraints
cp templates/constraints/docs/accuracy.yaml .arena/constraints/
cp templates/constraints/common/completeness.yaml .arena/constraints/

# Or use a preset (includes curated constraint set)
/arena:genloop-init --template docs
```

## Customization

Each constraint template includes:
- **id**: Unique identifier
- **priority**: 1-10 (lower = higher priority)
- **summary**: Brief description for the generator
- **rules**: Specific evaluation criteria with severity levels
- **agents**: (optional) Override which agents critique this constraint

Severity levels:
- **CRITICAL**: Must fix (blocks approval)
- **HIGH**: Should fix (usually blocks approval)
- **MEDIUM**: Consider fixing (may be dismissed)
- **LOW**: Nice to have (often dismissed)

## Agent Override Hierarchy

Which agents critique each constraint is resolved in this order:

1. **Per-constraint `agents:`** (highest priority)
   ```yaml
   # In constraint.yaml
   agents: [claude, codex]
   ```

2. **Pattern rules in genloop.yaml**
   ```yaml
   # In genloop.yaml
   constraints:
     routing:
       rules:
         - match: "security*"
           agents: [claude, codex]
   ```

3. **Default agents in genloop.yaml**
   ```yaml
   # In genloop.yaml
   constraints:
     routing:
       default_agents: [claude, codex, gemini]
   ```

4. **Built-in default**: `[claude, codex, gemini]`

**Tip:** To quickly switch agents (e.g., quota limits), edit `genloop.yaml` default_agents.
For constraint-specific needs, add `agents:` to that constraint file.
