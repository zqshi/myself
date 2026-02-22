from __future__ import annotations

from prompt_registry import compose_prompt


ACTIVE_SKILL = "execution"


def execute(task: dict, low_conf_threshold: float = 0.70) -> dict:
    """Return a structured result for the task.

    Placeholder execution while preserving prompt architecture:
    system persona + execution skill are composed via prompt_registry.
    """
    _prompt = compose_prompt(ACTIVE_SKILL)

    confidence = 0.78 if task.get("task_type") != "unknown" else 0.62
    need_human = confidence < low_conf_threshold

    conclusion = f"Completed initial draft for task {task['task_id']} ({task['task_type']})."
    evidence = [
        "Objective parsed and constraints acknowledged.",
        "Output generated in required section format.",
    ]
    risks = [
        "Assumptions may be incomplete if upstream context is missing.",
    ]
    next_actions = [
        "Review output and provide acceptance feedback.",
        "Escalate if redline topics are detected.",
    ]

    return {
        "conclusion": conclusion,
        "evidence": evidence,
        "risks": risks,
        "next_actions": next_actions,
        "confidence": round(confidence, 2),
        "need_human_decision": need_human,
        "need_human_reason": "Low confidence" if need_human else "No redline triggered",
        "prompt_profile": {"skill": ACTIVE_SKILL, "prompt_loaded": bool(_prompt)},
    }
