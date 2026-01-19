#!/usr/bin/env python3
"""
Agent Arena Human-in-the-Loop (HITL) Functions

Functions for handling human input requests and responses in the orchestration loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import (
    utc_now_iso, sha256, load_json, save_json_atomic, write_live,
)


def ingest_hitl_answers(state_dir: Path) -> Optional[Dict[str, Any]]:
    """Read and consume HITL answers. Returns answers or None."""
    answers_path = state_dir / "hitl" / "answers.json"
    if not answers_path.exists():
        return None

    answers = load_json(answers_path, None)
    if not answers:
        return None

    # Move to processed (don't delete, keep for audit)
    processed_path = state_dir / "hitl" / f"answers_{sha256(utc_now_iso())}.processed.json"
    answers_path.rename(processed_path)

    return answers


def write_hitl_questions(
    state_dir: Path, questions: List[Dict[str, Any]], turn: int
) -> None:
    """Write pending HITL questions and display them to user."""
    qpath = state_dir / "hitl" / "questions.json"
    save_json_atomic(
        qpath,
        {
            "timestamp": utc_now_iso(),
            "turn": turn,
            "questions": questions,
            "answer_format": {
                "answers": [{"question_id": "q1", "answer": "your answer"}]
            },
        },
    )

    # Display questions prominently
    print("\n" + "=" * 60)
    print("HUMAN INPUT NEEDED")
    print("=" * 60)
    write_live("=" * 50)
    write_live("HUMAN INPUT NEEDED")
    write_live("=" * 50)

    for agent_q in questions:
        agent = agent_q.get("agent", "unknown")
        agent_questions = agent_q.get("questions", [])
        print(f"\n[{agent}] asks:")
        write_live(f"\n[{agent}] asks:")

        for i, q in enumerate(agent_questions, 1):
            if isinstance(q, dict):
                q_text = q.get("question", q.get("text", str(q)))
                q_id = q.get("id", f"q{i}")
            else:
                q_text = str(q)
                q_id = f"q{i}"
            print(f"  [{q_id}] {q_text}")
            write_live(f"  [{q_id}] {q_text}")

    print(f"\nTo respond, edit: {qpath.parent / 'answers.json'}")
    print("Format: {\"answers\": [{\"question_id\": \"q1\", \"answer\": \"your answer\"}]}")
    print("Then re-run the orchestrator with the same --name")
    print("=" * 60 + "\n")
    write_live(f"\nEdit {qpath.parent / 'answers.json'} to respond")
    write_live("=" * 50)


def write_agent_result(
    run_dir: Path,
    status: str,
    exit_code: int,
    summary: Optional[str] = None,
    questions: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> None:
    """Write agent-result.json for SubagentStop hook consumption."""
    result = {
        "timestamp": utc_now_iso(),
        "run_name": run_dir.name,
        "status": status,
        "exit_code": exit_code,
        "questions": questions,
        "summary": summary,
    }
    if error:
        result["error"] = error
    save_json_atomic(run_dir / "agent-result.json", result)


def write_resolution(state_dir: Path, reason: str, turn: int, summary: str) -> None:
    """Write final resolution artifact."""
    save_json_atomic(
        state_dir / "resolution.json",
        {
            "timestamp": utc_now_iso(),
            "reason": reason,
            "final_turn": turn,
            "summary": summary,
        },
    )
