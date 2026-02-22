from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from command_guard import create_request, load_policy, DEFAULT_POLICY

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "memory" / "tasks.db"

TASK_HINTS = {
    "tech_scan": {
        "skill_query": "technology scouting trend analysis",
        "repo_query": 'tech scouting automation language:python stars:>100',
    },
    "meeting_minutes": {
        "skill_query": "meeting summarization action items",
        "repo_query": 'meeting minutes action extraction language:python stars:>100',
    },
    "prd_draft": {
        "skill_query": "product requirements drafting",
        "repo_query": 'product requirements generator language:typescript stars:>100',
    },
    "weekly_report": {
        "skill_query": "weekly reporting analytics",
        "repo_query": 'weekly report automation language:python stars:>100',
    },
    "unknown": {
        "skill_query": "general productivity workflow",
        "repo_query": 'workflow automation agent language:python stars:>100',
    },
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def failure_by_task_type(window: int = 200) -> list[dict]:
    q = """
    SELECT t.task_type, f.accepted, f.rework, f.escalation
    FROM feedback f
    JOIN episodes e ON e.episode_id = f.episode_id
    JOIN tasks t ON t.task_id = e.task_id
    ORDER BY f.created_at DESC
    LIMIT ?
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(q, (window,)).fetchall()]

    agg: dict[str, dict] = {}
    for r in rows:
        t = r.get("task_type") or "unknown"
        a = agg.setdefault(t, {"task_type": t, "total": 0, "rejected": 0, "rework": 0, "escalation": 0})
        a["total"] += 1
        a["rejected"] += 1 if int(r.get("accepted", 0)) == 0 else 0
        a["rework"] += 1 if int(r.get("rework", 0)) == 1 else 0
        a["escalation"] += 1 if int(r.get("escalation", 0)) == 1 else 0

    out = list(agg.values())
    for item in out:
        total = item["total"] or 1
        item["pain_score"] = round((item["rejected"] * 0.5 + item["rework"] * 0.3 + item["escalation"] * 0.2) / total, 4)
    out.sort(key=lambda x: x["pain_score"], reverse=True)
    return out


def build_growth_plan(window: int = 200, top_k: int = 3) -> dict:
    failures = failure_by_task_type(window=window)
    focus = failures[:top_k] if failures else [{"task_type": "unknown", "pain_score": 0.0}]

    items = []
    for f in focus:
        t = f["task_type"]
        hint = TASK_HINTS.get(t, TASK_HINTS["unknown"])
        items.append(
            {
                "task_type": t,
                "pain_score": f.get("pain_score", 0.0),
                "skill_search_command": f'npx skills find "{hint["skill_query"]}"',
                "github_search_command": f'gh search repos "{hint["repo_query"]}" --limit 20',
                "adoption_rule": "install/update must require approval",
            }
        )

    return {
        "window": window,
        "top_k": top_k,
        "focus_areas": items,
    }


def enqueue_growth_requests(plan: dict, cwd: Path, policy_path: Path = DEFAULT_POLICY) -> dict:
    policy = load_policy(Path(policy_path))
    created = []

    for item in plan.get("focus_areas", []):
        for cmd in (item["skill_search_command"], item["github_search_command"]):
            rec = create_request(policy, cmd, cwd.resolve(), reason=f"self-growth for {item['task_type']}")
            created.append(
                {
                    "request_id": rec["request_id"],
                    "command": cmd,
                    "status": rec["status"],
                    "decision": rec["decision"],
                }
            )

    return {"count": len(created), "requests": created}


def plan_json(window: int = 200, top_k: int = 3) -> str:
    return json.dumps(build_growth_plan(window=window, top_k=top_k), ensure_ascii=True)
