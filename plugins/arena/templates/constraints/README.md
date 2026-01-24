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
/arena:init --template docs
```

## Customization

Each constraint template includes:
- **id**: Unique identifier
- **priority**: 1-10 (lower = higher priority)
- **summary**: Brief description for the generator
- **rules**: Specific evaluation criteria with severity levels

Severity levels:
- **CRITICAL**: Must fix (blocks approval)
- **HIGH**: Should fix (usually blocks approval)
- **MEDIUM**: Consider fixing (may be dismissed)
- **LOW**: Nice to have (often dismissed)
