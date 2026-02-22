from __future__ import annotations

from prompt_registry import compose_prompt


ACTIVE_SKILL = "training"


def propose_change(episodes: list[dict], feedback: list[dict]) -> dict:
    """Simple heuristic proposal placeholder."""
    _prompt = compose_prompt(ACTIVE_SKILL)

    total = len(episodes)
    rework_count = sum(1 for x in feedback if x.get("rework"))
    rework_rate = (rework_count / total) if total else 0.0

    if total == 0:
        return {
            "hypothesis": "Insufficient data for policy update.",
            "proposed_diff": "none",
            "expected_metric_impact": "none",
            "projected_delta": {
                "acceptance_rate": 0.0,
                "first_pass_rate": 0.0,
                "rework_rate": 0.0,
                "escalation_rate": 0.0,
            },
            "risk_of_regression": "low",
            "rollout_plan": "collect more data",
            "prompt_profile": {"skill": ACTIVE_SKILL, "prompt_loaded": bool(_prompt)},
        }

    if rework_rate >= 0.30:
        delta = {
            "acceptance_rate": 0.06,
            "first_pass_rate": 0.06,
            "rework_rate": -0.08,
            "escalation_rate": -0.02,
        }
        hypothesis = "Strengthen evidence and assumptions checklist to reduce rework."
    else:
        delta = {
            "acceptance_rate": 0.02,
            "first_pass_rate": 0.02,
            "rework_rate": -0.03,
            "escalation_rate": 0.0,
        }
        hypothesis = "Tighten task routing and reduce ambiguous outputs."

    return {
        "hypothesis": hypothesis,
        "proposed_diff": "critic requires at least 2 concrete evidence points",
        "expected_metric_impact": f"rework_rate improvement target from observed {rework_count}/{total}",
        "projected_delta": delta,
        "risk_of_regression": "medium",
        "rollout_plan": "offline benchmark then 20% canary",
        "prompt_profile": {"skill": ACTIVE_SKILL, "prompt_loaded": bool(_prompt)},
    }
