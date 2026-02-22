from __future__ import annotations


def review(task: dict, result: dict) -> dict:
    findings = []
    required = ["conclusion", "evidence", "risks", "next_actions", "confidence", "need_human_decision"]
    missing = [k for k in required if k not in result]
    if missing:
        findings.append(f"Missing required fields: {', '.join(missing)}")

    score = 100 - (len(missing) * 20)
    escalation = bool(result.get("need_human_decision", False))

    return {
        "score": max(score, 0),
        "findings": findings,
        "missing_evidence": [],
        "escalation_needed": escalation,
        "revision_guidance": ["Add more concrete evidence if score < 90"] if score < 90 else [],
    }
