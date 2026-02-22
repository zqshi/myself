#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path

from critic import review
from doer import execute
from gatekeeper import decide_release
from metrics import apply_projection, summarize
from trainer import propose_change

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "memory" / "tasks.db"
SQL_INIT_PATH = ROOT / "memory" / "init_db.sql"
EPISODES_JSONL = ROOT / "memory" / "episodes.jsonl"
FEEDBACK_JSONL = ROOT / "memory" / "feedback.jsonl"
POLICY_STATE_PATH = ROOT / "memory" / "policy_state.json"
THRESHOLDS_PATH = ROOT / "configs" / "thresholds.yaml"
BENCHMARK_PATH = ROOT / "eval" / "benchmark_set.jsonl"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    sql = SQL_INIT_PATH.read_text(encoding="utf-8")
    with connect() as conn:
        conn.executescript(sql)

    if not POLICY_STATE_PATH.exists():
        POLICY_STATE_PATH.write_text(
            json.dumps({"active_policy_version": "v0.1.0", "last_change_id": None}, indent=2),
            encoding="utf-8",
        )


def load_policy_state() -> dict:
    if not POLICY_STATE_PATH.exists():
        return {"active_policy_version": "v0.1.0", "last_change_id": None}
    return json.loads(POLICY_STATE_PATH.read_text(encoding="utf-8"))


def save_policy_state(state: dict) -> None:
    POLICY_STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def active_policy_version() -> str:
    return str(load_policy_state().get("active_policy_version", "v0.1.0"))


def parse_simple_yaml_thresholds(path: Path) -> dict:
    # Minimal YAML parser for this project's simple key/value config.
    out: dict = {}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw.startswith("  ") and section and ":" in line:
            k, v = line.split(":", 1)
            out.setdefault(section, {})[k.strip()] = float(v.strip())
            continue
        if line.endswith(":"):
            section = line[:-1]
            out.setdefault(section, {})
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = float(v.strip())
            section = None
    return out


def run_offline_eval() -> dict:
    total = 0
    passed = 0
    with BENCHMARK_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            item = json.loads(line)
            required = set(item.get("expected", {}).get("must_include", []))
            if required:
                passed += 1

    score = (passed / total) if total else 0.0
    return {"total": total, "passed": passed, "score": round(score, 4)}


def bump_patch(version: str) -> str:
    # v0.1.0 -> v0.1.1
    core = version[1:] if version.startswith("v") else version
    parts = core.split(".")
    if len(parts) != 3:
        return "v0.1.0"
    major, minor, patch = (int(parts[0]), int(parts[1]), int(parts[2]))
    return f"v{major}.{minor}.{patch + 1}"


def append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def add_task(source: str, task_type: str, objective: str, constraints: str, deadline: str | None) -> str:
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks(task_id, source, task_type, objective, constraints, deadline, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (task_id, source, task_type, objective, constraints, deadline, ts, ts),
        )
    return task_id


def ingest_inbox(path: Path, default_source: str) -> list[str]:
    created: list[str] = []
    if not path.exists():
        return created

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            task_id = add_task(
                source=item.get("source", default_source),
                task_type=item.get("task_type", "unknown"),
                objective=item.get("objective", ""),
                constraints=item.get("constraints", ""),
                deadline=item.get("deadline"),
            )
            created.append(task_id)
    return created


def list_tasks(status: str | None) -> list[sqlite3.Row]:
    q = "SELECT * FROM tasks"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    q += " ORDER BY created_at DESC"
    with connect() as conn:
        return list(conn.execute(q, params).fetchall())


def load_task(task_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()


def load_feedback_rows(limit: int = 100, policy_version: str | None = None) -> list[dict]:
    if policy_version:
        q = """
        SELECT f.accepted, f.rework, f.escalation
        FROM feedback f
        JOIN episodes e ON e.episode_id = f.episode_id
        WHERE e.policy_version = ?
        ORDER BY f.created_at DESC
        LIMIT ?
        """
        params: tuple = (policy_version, limit)
    else:
        q = "SELECT accepted, rework, escalation FROM feedback ORDER BY created_at DESC LIMIT ?"
        params = (limit,)

    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def load_episodes(limit: int = 100, policy_version: str | None = None) -> list[dict]:
    if policy_version:
        q = "SELECT * FROM episodes WHERE policy_version = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple = (policy_version, limit)
    else:
        q = "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def run_once(task_id: str) -> dict:
    policy_version = active_policy_version()
    with connect() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not task:
            raise ValueError(f"task not found: {task_id}")

        if task["status"] not in ("queued", "failed"):
            raise ValueError(f"task status must be queued/failed, got {task['status']}")

        conn.execute("UPDATE tasks SET status='running', updated_at=? WHERE task_id=?", (now_iso(), task_id))

    task_dict = dict(task)
    result = execute(task_dict)
    critique = review(task_dict, result)

    episode_id = f"ep-{uuid.uuid4().hex[:8]}"
    feedback_id = f"fb-{uuid.uuid4().hex[:8]}"
    ts = now_iso()

    accepted = int(critique["score"] >= 80 and not critique["escalation_needed"])
    rework = int(not accepted)
    escalation = int(bool(critique["escalation_needed"]))
    outcome = "accepted" if accepted else "needs_review"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO episodes(episode_id, task_id, policy_version, skill_version, plan, actions, artifacts, outcome, confidence, duration_sec, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                task_id,
                policy_version,
                "v0",
                json.dumps({"objective": task_dict["objective"]}),
                json.dumps(["doer.execute", "critic.review"]),
                json.dumps({"result": result, "critique": critique}),
                outcome,
                float(result["confidence"]),
                1,
                ts,
            ),
        )

        conn.execute(
            """
            INSERT INTO feedback(feedback_id, episode_id, accepted, edits_count, rework, escalation, human_comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, episode_id, accepted, 0, rework, escalation, "auto-generated", ts),
        )

        next_status = "done" if accepted else "needs_human"
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE task_id=?", (next_status, ts, task_id))

    episode_record = {
        "episode_id": episode_id,
        "task_id": task_id,
        "policy_version": policy_version,
        "result": result,
        "critique": critique,
        "outcome": outcome,
        "created_at": ts,
    }
    feedback_record = {
        "feedback_id": feedback_id,
        "episode_id": episode_id,
        "accepted": bool(accepted),
        "rework": bool(rework),
        "escalation": bool(escalation),
        "created_at": ts,
    }

    append_jsonl(EPISODES_JSONL, episode_record)
    append_jsonl(FEEDBACK_JSONL, feedback_record)

    return {
        "task_id": task_id,
        "episode_id": episode_id,
        "policy_version": policy_version,
        "status": outcome,
        "confidence": result["confidence"],
    }


def run_queued(limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT task_id FROM tasks WHERE status='queued' ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()
    outputs = []
    for r in rows:
        outputs.append(run_once(r["task_id"]))
    return outputs


def compute_metrics(limit: int, policy_version: str | None) -> dict:
    rows = load_feedback_rows(limit=limit, policy_version=policy_version)
    metrics = summarize(rows)
    metrics["policy_version"] = policy_version or "all"
    return metrics


def train_cycle(window: int, candidate_version: str | None, auto_activate: bool) -> dict:
    from_version = active_policy_version()
    to_version = candidate_version or bump_patch(from_version)

    episodes = load_episodes(limit=window, policy_version=from_version)
    feedback = load_feedback_rows(limit=window, policy_version=from_version)
    proposal = propose_change(episodes, feedback)

    baseline_metrics = summarize(feedback)
    candidate_metrics = apply_projection(baseline_metrics, proposal.get("projected_delta", {}))

    thresholds = parse_simple_yaml_thresholds(THRESHOLDS_PATH)
    decision = decide_release(baseline_metrics, candidate_metrics, thresholds)

    offline_eval = run_offline_eval()
    if offline_eval["score"] < 0.80 and decision["decision"] == "approve":
        decision = {
            "decision": "reject",
            "reason": "Offline benchmark score below 0.80",
            "metric_comparison": {
                "offline_score": offline_eval["score"],
                "required": 0.80,
            },
            "next_step": "Improve proposal and rerun benchmark",
        }

    change_id = f"chg-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO policy_changes(change_id, from_version, to_version, hypothesis, diff, offline_score, canary_score, decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_id,
                from_version,
                to_version,
                proposal.get("hypothesis", ""),
                json.dumps(proposal, ensure_ascii=True),
                float(offline_eval["score"]),
                float(candidate_metrics.get("acceptance_rate", 0.0)),
                decision["decision"],
                ts,
            ),
        )

    promoted = False
    if auto_activate and decision["decision"] == "approve":
        state = load_policy_state()
        state["active_policy_version"] = to_version
        state["last_change_id"] = change_id
        save_policy_state(state)
        promoted = True

    return {
        "change_id": change_id,
        "from_version": from_version,
        "to_version": to_version,
        "proposal": proposal,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "offline_eval": offline_eval,
        "decision": decision,
        "auto_activated": promoted,
    }


def list_policy_changes(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM policy_changes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def activate_policy(version: str, change_id: str | None) -> dict:
    state = load_policy_state()
    state["active_policy_version"] = version
    if change_id:
        state["last_change_id"] = change_id
    save_policy_state(state)
    return state


def cmd_init_db(_: argparse.Namespace) -> None:
    init_db()
    print(f"initialized db: {DB_PATH}")


def cmd_add_task(args: argparse.Namespace) -> None:
    task_id = add_task(args.source, args.task_type, args.objective, args.constraints, args.deadline)
    print(task_id)


def cmd_ingest_inbox(args: argparse.Namespace) -> None:
    path = Path(args.path)
    created = ingest_inbox(path, args.default_source)
    print(json.dumps({"ingested": len(created), "task_ids": created}, ensure_ascii=True))


def cmd_list_tasks(args: argparse.Namespace) -> None:
    rows = list_tasks(args.status)
    for r in rows:
        print(f"{r['task_id']}\t{r['status']}\t{r['task_type']}\t{r['objective']}")


def cmd_run_once(args: argparse.Namespace) -> None:
    result = run_once(args.task_id)
    print(json.dumps(result, ensure_ascii=True))


def cmd_run_queued(args: argparse.Namespace) -> None:
    outputs = run_queued(args.limit)
    print(json.dumps({"count": len(outputs), "results": outputs}, ensure_ascii=True))


def cmd_metrics(args: argparse.Namespace) -> None:
    m = compute_metrics(limit=args.limit, policy_version=args.policy_version)
    print(json.dumps(m, ensure_ascii=True))


def cmd_train_cycle(args: argparse.Namespace) -> None:
    result = train_cycle(window=args.window, candidate_version=args.to_version, auto_activate=args.auto_activate)
    print(json.dumps(result, ensure_ascii=True))


def cmd_list_policy_changes(args: argparse.Namespace) -> None:
    rows = list_policy_changes(limit=args.limit)
    print(json.dumps({"count": len(rows), "changes": rows}, ensure_ascii=True))


def cmd_activate_policy(args: argparse.Namespace) -> None:
    state = activate_policy(version=args.version, change_id=args.change_id)
    print(json.dumps(state, ensure_ascii=True))


def cmd_policy_state(_: argparse.Namespace) -> None:
    print(json.dumps(load_policy_state(), ensure_ascii=True))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Self-evolving agent orchestrator")
    sub = p.add_subparsers(required=True)

    p_init = sub.add_parser("init-db")
    p_init.set_defaults(func=cmd_init_db)

    p_add = sub.add_parser("add-task")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--task-type", required=True)
    p_add.add_argument("--objective", required=True)
    p_add.add_argument("--constraints", default="")
    p_add.add_argument("--deadline", default=None)
    p_add.set_defaults(func=cmd_add_task)

    p_ingest = sub.add_parser("ingest-inbox")
    p_ingest.add_argument("--path", required=True)
    p_ingest.add_argument("--default-source", default="inbox")
    p_ingest.set_defaults(func=cmd_ingest_inbox)

    p_list = sub.add_parser("list-tasks")
    p_list.add_argument("--status", default=None)
    p_list.set_defaults(func=cmd_list_tasks)

    p_run = sub.add_parser("run-once")
    p_run.add_argument("--task-id", required=True)
    p_run.set_defaults(func=cmd_run_once)

    p_runq = sub.add_parser("run-queued")
    p_runq.add_argument("--limit", type=int, default=10)
    p_runq.set_defaults(func=cmd_run_queued)

    p_m = sub.add_parser("metrics")
    p_m.add_argument("--limit", type=int, default=100)
    p_m.add_argument("--policy-version", default=None)
    p_m.set_defaults(func=cmd_metrics)

    p_train = sub.add_parser("train-cycle")
    p_train.add_argument("--window", type=int, default=100)
    p_train.add_argument("--to-version", default=None)
    p_train.add_argument("--auto-activate", action="store_true")
    p_train.set_defaults(func=cmd_train_cycle)

    p_lpc = sub.add_parser("list-policy-changes")
    p_lpc.add_argument("--limit", type=int, default=20)
    p_lpc.set_defaults(func=cmd_list_policy_changes)

    p_act = sub.add_parser("activate-policy")
    p_act.add_argument("--version", required=True)
    p_act.add_argument("--change-id", default=None)
    p_act.set_defaults(func=cmd_activate_policy)

    p_ps = sub.add_parser("policy-state")
    p_ps.set_defaults(func=cmd_policy_state)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
