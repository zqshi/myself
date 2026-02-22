from __future__ import annotations


def safe_div(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def summarize(feedback_rows: list[dict]) -> dict:
    total = len(feedback_rows)
    accepted = sum(int(x.get("accepted", 0)) for x in feedback_rows)
    rework = sum(int(x.get("rework", 0)) for x in feedback_rows)
    escalation = sum(int(x.get("escalation", 0)) for x in feedback_rows)

    return {
        "total": total,
        "acceptance_rate": round(safe_div(accepted, total), 4),
        "first_pass_rate": round(safe_div(accepted, total), 4),
        "rework_rate": round(safe_div(rework, total), 4),
        "escalation_rate": round(safe_div(escalation, total), 4),
    }


def apply_projection(baseline: dict, delta: dict) -> dict:
    out = dict(baseline)
    for k, dv in delta.items():
        if k not in out:
            continue
        out[k] = round(min(max(float(out[k]) + float(dv), 0.0), 1.0), 4)
    return out
