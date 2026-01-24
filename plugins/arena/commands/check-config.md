---
description: Validate genloop configuration and constraint files
argument-hint: [<dir>]
allowed-tools: Bash, Read, Glob
---

# Arena Check Config

Validate genloop configuration files for correctness before running.

## Usage

```
/arena:check-config              # Check .arena/ in current directory
/arena:check-config .arena/      # Explicit path
/arena:check-config path/to/dir  # Check specific directory
```

## Workflow

### Phase 1: Determine Directory

If `$ARGUMENTS` is empty:
- Default to `.arena/`

Otherwise:
- Use the provided path

### Phase 2: Check Directory Structure

Verify expected structure exists:

```bash
ls -la <dir>/
```

Expected files:
- `genloop.yaml` (required) - Main configuration
- `constraints/` (required) - Directory with constraint files

Report missing files:
```
✗ Missing genloop.yaml
✗ Missing constraints/ directory
```

### Phase 3: Validate genloop.yaml

Read and validate the genloop configuration:

```
Read("<dir>/genloop.yaml")
```

**Check required structure:**
- Must be valid YAML
- `max_iterations` (if present): integer 1-20
- `constraints.dir` or `constraints.files`: must specify constraint location
- `phases` (if present): valid agent names (claude, codex, gemini)
- `termination.approve_when` (if present): valid policy name

**Check agent names are valid:**
Valid agents: `claude`, `codex`, `gemini`

Check in:
- `phases.generate.agent`
- `phases.adjudicate.agent`
- `phases.refine.agent`
- `constraints.routing.default_agents[]`
- `constraints.routing.rules[].agents[]`

**Report issues:**
```
✓ genloop.yaml is valid YAML
✓ max_iterations: 5 (valid)
✗ phases.generate.agent: "gpt4" is not a valid agent (use: claude, codex, gemini)
✓ constraints.routing.default_agents: [claude, codex, gemini]
```

### Phase 4: Validate Constraint Files

Find all constraint files:

```bash
find <dir>/constraints -name "*.yaml" -type f
```

For each constraint file, read and validate:

**Required fields:**
- `id` (string, required) - Unique identifier
- `priority` (integer 1-10, required) - Lower = higher priority
- `summary` (string, required) - Description for generator
- `rules` (array, required) - At least one rule

**Optional fields:**
- `agents` (array of valid agent names)
- `source` (object with files/globs/scripts/inline)

**Rule validation:**
Each rule must have:
- `id` (string, required)
- `text` (string, required)
- `default_severity` (one of: CRITICAL, HIGH, MEDIUM, LOW)

**Report per file:**
```
Checking constraints/accuracy.yaml...
  ✓ id: accuracy
  ✓ priority: 1
  ✓ summary: present
  ✓ rules: 3 rules defined
  ✓ agents: [claude, codex] (override)

Checking constraints/security.yaml...
  ✓ id: security
  ✓ priority: 1
  ✗ rules[0].default_severity: "IMPORTANT" is not valid (use: CRITICAL, HIGH, MEDIUM, LOW)
```

### Phase 5: Check Cross-References

**Check routing patterns match constraints:**
If genloop.yaml has routing rules with patterns:
```yaml
constraints:
  routing:
    rules:
      - match: "security*"
        agents: [claude, codex]
```

Verify at least one constraint ID matches each pattern:
```
✓ Pattern "security*" matches: security
✗ Pattern "perf*" matches no constraints (warning)
```

### Phase 6: Summary Report

Output final summary:

```
═══════════════════════════════════════════════════════════
  GENLOOP CONFIG VALIDATION REPORT
═══════════════════════════════════════════════════════════

  Directory: .arena/

  Configuration:
    ✓ genloop.yaml - valid

  Constraints: 3 files checked
    ✓ accuracy.yaml - valid
    ✓ clarity.yaml - valid
    ✗ security.yaml - 1 error

  Cross-references:
    ✓ All routing patterns match at least one constraint

═══════════════════════════════════════════════════════════
  RESULT: 1 error, 0 warnings

  Fix the errors above before running /arena:genloop
═══════════════════════════════════════════════════════════
```

Or if all valid:

```
═══════════════════════════════════════════════════════════
  GENLOOP CONFIG VALIDATION REPORT
═══════════════════════════════════════════════════════════

  Directory: .arena/

  Configuration:
    ✓ genloop.yaml - valid (max_iterations: 5)

  Constraints: 3 files checked
    ✓ accuracy.yaml (priority 1, 4 rules)
    ✓ clarity.yaml (priority 3, 5 rules)
    ✓ security.yaml (priority 1, 6 rules, agents: [claude, codex])

  Agents configured:
    • Generate: claude
    • Adjudicate: claude
    • Critique (default): claude, codex, gemini
    • Critique (security*): claude, codex

═══════════════════════════════════════════════════════════
  RESULT: ✓ All checks passed

  Ready to run: /arena:genloop <name> --goal "..."
═══════════════════════════════════════════════════════════
```

## Validation Rules Reference

### genloop.yaml

| Field | Type | Valid Values |
|-------|------|--------------|
| `max_iterations` | int | 1-20 |
| `phases.*.agent` | string | claude, codex, gemini |
| `constraints.routing.default_agents` | array | [claude, codex, gemini] |
| `termination.approve_when` | string | no_critical, no_critical_and_no_high, all_resolved |
| `phases.refine.mode` | string | edit, rewrite |

### constraint.yaml

| Field | Type | Required | Valid Values |
|-------|------|----------|--------------|
| `id` | string | yes | unique identifier |
| `priority` | int | yes | 1-10 |
| `summary` | string | yes | non-empty |
| `rules` | array | yes | at least 1 rule |
| `agents` | array | no | [claude, codex, gemini] |
| `rules[].id` | string | yes | unique within constraint |
| `rules[].text` | string | yes | non-empty |
| `rules[].default_severity` | string | yes | CRITICAL, HIGH, MEDIUM, LOW |
