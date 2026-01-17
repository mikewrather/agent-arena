# Modes Reference

Modes define how agents interact with each other.

## Adversarial Mode

Agents actively challenge each other's conclusions:
- Look for flaws and weaknesses in other agents' analysis
- Propose alternative interpretations
- Play devil's advocate
- Push back on premature consensus

Best for: Security audits, finding bugs, stress-testing ideas

## Collaborative Mode

Agents build on each other's ideas:
- Extend and refine previous contributions
- Look for synergies between perspectives
- Synthesize diverse viewpoints
- Work toward shared understanding

Best for: Brainstorming, improvement, creative tasks

## Custom Modes

Create project-specific modes in `.arena/modes/<name>.md`:

```markdown
---
name: my-mode
pattern: parallel
---

# My Custom Mode

Instructions for how agents should behave...
```

Project modes override plugin modes with the same name.
