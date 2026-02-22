from __future__ import annotations


def decide_release(baseline: dict, candidate: dict, thresholds: dict) -> dict:
    """Rule-based release decision."""
    accept_floor = float(thresholds.get("acceptance_rate_min", 0.75))
    first_pass_floor = float(thresholds.get("first_pass_rate_min", 0.60))
    rework_ceiling = float(thresholds.get("rework_rate_max", 0.25))
    escalation_ceiling = float(thresholds.get("escalation_rate_max", 0.20))
    rollback_drop = float(thresholds.get("rollback_if", {}).get("acceptance_drop_gt", 0.05))

    b = float(baseline.get("acceptance_rate", 0.0))
    c = float(candidate.get("acceptance_rate", 0.0))
    c_first = float(candidate.get("first_pass_rate", 0.0))
    c_rework = float(candidate.get("rework_rate", 1.0))
    c_escalation = float(candidate.get("escalation_rate", 1.0))

    if c < accept_floor:
        return {
            "decision": "reject",
            "reason": "Candidate acceptance rate below floor",
            "metric_comparison": {"baseline": b, "candidate": c},
            "next_step": "Revise proposal and rerun offline eval",
        }

    if c_first < first_pass_floor:
        return {
            "decision": "reject",
            "reason": "Candidate first-pass rate below floor",
            "metric_comparison": {"candidate_first_pass_rate": c_first, "required": first_pass_floor},
            "next_step": "Improve routing/prompt and retry",
        }

    if c_rework > rework_ceiling:
        return {
            "decision": "reject",
            "reason": "Candidate rework rate above ceiling",
            "metric_comparison": {"candidate_rework_rate": c_rework, "max": rework_ceiling},
            "next_step": "Strengthen checks before release",
        }

    if c_escalation > escalation_ceiling:
        return {
            "decision": "reject",
            "reason": "Candidate escalation rate above ceiling",
            "metric_comparison": {"candidate_escalation_rate": c_escalation, "max": escalation_ceiling},
            "next_step": "Reduce ambiguity and uncertainty handling",
        }

    if (b - c) > rollback_drop:
        return {
            "decision": "rollback",
            "reason": "Acceptance drop exceeds rollback threshold",
            "metric_comparison": {"baseline": b, "candidate": c},
            "next_step": "Rollback to last stable policy version",
        }

    return {
        "decision": "approve",
        "reason": "Candidate meets release thresholds",
        "metric_comparison": {"baseline": b, "candidate": c},
        "next_step": "Promote to canary",
    }
