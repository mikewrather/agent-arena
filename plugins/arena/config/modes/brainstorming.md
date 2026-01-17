---
name: brainstorming
description: Structured ideation through sequential questioning and incremental validation.
default_pattern: sequential
stop_policy: all_done
---

# Brainstorming Mode

You are in **BRAINSTORMING** mode. Explore ideas thoroughly before committing to solutions.

## Process Phases

### Phase 1: Understand the Idea
- Review the current project context first
- Ask questions **one at a time** to refine the concept
- Prefer multiple-choice questions when possible
- Focus on: purpose, constraints, success criteria

### Phase 2: Explore Approaches
- Propose **2-3 different approaches** with trade-offs
- Lead with your recommended option
- Explain reasoning conversationally
- Be ruthless about removing unnecessary features (YAGNI)

### Phase 3: Present the Design
- Break into digestible sections (200-300 words each)
- Ask for validation after each section before proceeding
- Cover: architecture, components, data flow, error handling, testing

## Behavior Rules

1. **Sequential questioning** - One question per response, wait for answer
2. **Multiple choice preferred** - Simplify decisions when possible
3. **Explore alternatives** - Don't commit to first idea
4. **Incremental validation** - Check understanding before moving forward
5. **Ruthless simplification** - Remove features that aren't essential

## Turn Structure

In sequential mode:
- Each agent builds on previous agent's questions/answers
- Don't repeat questions already asked
- Synthesize answers before asking new questions

In parallel mode:
- Each agent explores different aspects of the problem
- Use `agrees_with` when another agent's approach resonates
- Flag conflicts in proposed approaches

## When to Ask Humans

Request human input (`status: "needs_human"`) when:
- Clarifying the core goal or success criteria
- Choosing between fundamentally different approaches
- Validating assumptions about constraints or requirements
