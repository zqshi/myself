#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import orchestrator
from command_guard import create_request, load_policy, load_requests, save_requests, execute_command, decide, DEFAULT_POLICY
from prompt_registry import prompt_manifest
from self_growth import build_growth_plan, enqueue_growth_requests
from project_registry import discover_projects, get_project, load_registry, save_registry, DEFAULT_ROOT

ROOT = Path(__file__).resolve().parent.parent
DAILY_RUN = ROOT / "scripts" / "daily-run.sh"


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def agent_status() -> dict:
    policy_state = orchestrator.load_policy_state()
    queued = len(orchestrator.list_tasks("queued"))
    needs_human = len(orchestrator.list_tasks("needs_human"))
    metrics = orchestrator.compute_metrics(limit=200, policy_version=None)
    pending_requests = len([x for x in load_requests().values() if x.get("status") == "pending"])

    return {
        "policy_state": policy_state,
        "tasks": {"queued": queued, "needs_human": needs_human},
        "metrics": metrics,
        "pending_command_requests": pending_requests,
    }


def cmd_status(_: argparse.Namespace) -> None:
    print(json.dumps(agent_status(), ensure_ascii=True))


def cmd_show_prompts(_: argparse.Namespace) -> None:
    print(json.dumps(prompt_manifest(), ensure_ascii=True))


def cmd_growth_plan(args: argparse.Namespace) -> None:
    print(json.dumps(build_growth_plan(window=args.window, top_k=args.top_k), ensure_ascii=True))


def cmd_growth_request(args: argparse.Namespace) -> None:
    plan = build_growth_plan(window=args.window, top_k=args.top_k)
    out = enqueue_growth_requests(plan, cwd=Path(args.cwd), policy_path=Path(args.policy))
    print(json.dumps({"plan": plan, "enqueued": out}, ensure_ascii=True))


def cmd_projects_discover(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    projects = discover_projects(root=root, max_depth=args.max_depth)
    reg = save_registry(projects, root=root)
    print(json.dumps(reg, ensure_ascii=True))


def cmd_projects_list(args: argparse.Namespace) -> None:
    reg = load_registry()
    projects = reg.get("projects", [])
    if args.enabled_only:
        projects = [p for p in projects if p.get("enabled", True)]
    print(json.dumps({"root_path": reg.get("root_path"), "count": len(projects), "projects": projects}, ensure_ascii=True))


def cmd_project_command_request(args: argparse.Namespace) -> None:
    project = get_project(args.project_id)
    if not project:
        raise SystemExit(json.dumps({"error": f"project_id not found: {args.project_id}"}))
    policy = load_policy(Path(args.policy))
    rec = create_request(
        policy,
        args.command,
        Path(project["repo_path"]).resolve(),
        reason=args.reason or f"project command: {args.project_id}",
    )
    rec["project_id"] = args.project_id
    print(json.dumps(rec, ensure_ascii=True))


def cmd_project_add_task(args: argparse.Namespace) -> None:
    project = get_project(args.project_id)
    if not project:
        raise SystemExit(json.dumps({"error": f"project_id not found: {args.project_id}"}))
    payload = {
        "project_id": args.project_id,
        "repo_path": project["repo_path"],
        "extra_constraints": args.constraints or "",
    }
    task_id = orchestrator.add_task(
        source=f"project:{args.project_id}",
        task_type=args.task_type,
        objective=args.objective,
        constraints=json.dumps(payload, ensure_ascii=True),
        deadline=args.deadline,
    )
    print(json.dumps({"task_id": task_id, "project": project}, ensure_ascii=True))


def cmd_run(args: argparse.Namespace) -> None:
    cmd = ["bash", str(DAILY_RUN), "--limit", str(args.limit), "--window", str(args.window)]
    if args.inbox:
        cmd.extend(["--inbox", args.inbox])
    if args.auto_activate:
        cmd.append("--auto-activate")
    if args.skip_ingest:
        cmd.append("--skip-ingest")

    cp = run_cmd(cmd)
    out = {
        "exit_code": cp.returncode,
        "stdout": cp.stdout[-2000:],
        "stderr": cp.stderr[-2000:],
    }
    print(json.dumps(out, ensure_ascii=True))


def cmd_ingest(args: argparse.Namespace) -> None:
    created = orchestrator.ingest_inbox(Path(args.path), default_source=args.default_source)
    print(json.dumps({"ingested": len(created), "task_ids": created}, ensure_ascii=True))


def cmd_run_queued(args: argparse.Namespace) -> None:
    results = orchestrator.run_queued(args.limit)
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=True))


def cmd_evolve(args: argparse.Namespace) -> None:
    result = orchestrator.train_cycle(window=args.window, candidate_version=args.to_version, auto_activate=args.auto_activate)
    print(json.dumps(result, ensure_ascii=True))


def cmd_command_request(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    rec = create_request(policy, args.command, Path(args.cwd).resolve(), reason=args.reason)
    print(json.dumps(rec, ensure_ascii=True))


def cmd_command_approve(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    requests = load_requests()
    rec = requests.get(args.request_id)
    if not rec:
        raise SystemExit(json.dumps({"error": f"request not found: {args.request_id}"}))
    if rec.get("decision", {}).get("action") != "require_approval":
        raise SystemExit(json.dumps({"error": "request does not require approval"}))

    rec["approved_by"] = args.approved_by
    rec["approval_reason"] = args.reason
    rec["status"] = "approved"

    if args.execute:
        result = execute_command(policy, rec, approved_by=args.approved_by, approval_reason=args.reason)
        rec.update(result)
        rec["status"] = "executed"

    requests[args.request_id] = rec
    save_requests(requests)
    print(json.dumps(rec, ensure_ascii=True))


def cmd_command_exec(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    cwd = Path(args.cwd).resolve()
    decision_result = decide(policy, args.command, cwd)

    if decision_result["action"] == "deny":
        print(json.dumps({"status": "blocked", "decision": decision_result}, ensure_ascii=True))
        return

    rec = create_request(policy, args.command, cwd, reason=args.reason)

    if decision_result["action"] == "require_approval" and not (args.approved_by and args.approval_reason):
        print(json.dumps({"status": "awaiting_approval", "request": rec}, ensure_ascii=True))
        return

    result = execute_command(policy, rec, approved_by=args.approved_by, approval_reason=args.approval_reason)
    requests = load_requests()
    rec.update(result)
    rec["status"] = "executed"
    requests[rec["request_id"]] = rec
    save_requests(requests)

    print(json.dumps(rec, ensure_ascii=True))


class AgentAPIHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._json(200, agent_status())
            return
        if self.path == "/projects":
            reg = load_registry()
            self._json(200, reg)
            return
        if self.path.startswith("/growth/plan"):
            self._json(200, build_growth_plan(window=200, top_k=3))
            return
        if self.path.startswith("/commands/pending"):
            pending = [x for x in load_requests().values() if x.get("status") == "pending"]
            self._json(200, {"count": len(pending), "requests": pending})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            if self.path == "/ingest":
                path = payload.get("path")
                source = payload.get("default_source", "api")
                if not path:
                    self._json(400, {"error": "path required"})
                    return
                ids = orchestrator.ingest_inbox(Path(path), default_source=source)
                self._json(200, {"ingested": len(ids), "task_ids": ids})
                return

            if self.path == "/run":
                limit = int(payload.get("limit", 20))
                res = orchestrator.run_queued(limit)
                self._json(200, {"count": len(res), "results": res})
                return

            if self.path == "/evolve":
                window = int(payload.get("window", 200))
                auto_activate = bool(payload.get("auto_activate", False))
                to_version = payload.get("to_version")
                out = orchestrator.train_cycle(window=window, candidate_version=to_version, auto_activate=auto_activate)
                self._json(200, out)
                return

            if self.path == "/commands/request":
                policy = load_policy(Path(payload.get("policy", str(DEFAULT_POLICY))))
                command = payload.get("command")
                cwd = Path(payload.get("cwd", str(ROOT.parent))).resolve()
                reason = payload.get("reason")
                if not command:
                    self._json(400, {"error": "command required"})
                    return
                rec = create_request(policy, command, cwd, reason=reason)
                self._json(200, rec)
                return

            if self.path == "/commands/approve":
                policy = load_policy(Path(payload.get("policy", str(DEFAULT_POLICY))))
                request_id = payload.get("request_id")
                approved_by = payload.get("approved_by")
                reason = payload.get("reason")
                execute = bool(payload.get("execute", True))
                if not request_id or not approved_by or not reason:
                    self._json(400, {"error": "request_id, approved_by, reason required"})
                    return
                requests = load_requests()
                rec = requests.get(request_id)
                if not rec:
                    self._json(404, {"error": "request not found"})
                    return
                rec["approved_by"] = approved_by
                rec["approval_reason"] = reason
                rec["status"] = "approved"
                if execute:
                    result = execute_command(policy, rec, approved_by=approved_by, approval_reason=reason)
                    rec.update(result)
                    rec["status"] = "executed"
                requests[request_id] = rec
                save_requests(requests)
                self._json(200, rec)
                return

            if self.path == "/growth/request":
                policy = Path(payload.get("policy", str(DEFAULT_POLICY)))
                cwd = Path(payload.get("cwd", str(ROOT.parent)))
                window = int(payload.get("window", 200))
                top_k = int(payload.get("top_k", 3))
                plan = build_growth_plan(window=window, top_k=top_k)
                out = enqueue_growth_requests(plan, cwd=cwd, policy_path=policy)
                self._json(200, {"plan": plan, "enqueued": out})
                return

            if self.path == "/projects/discover":
                root = Path(payload.get("root", str(DEFAULT_ROOT)))
                max_depth = int(payload.get("max_depth", 2))
                projects = discover_projects(root=root, max_depth=max_depth)
                reg = save_registry(projects, root=root)
                self._json(200, reg)
                return

            if self.path == "/projects/command-request":
                project_id = payload.get("project_id")
                command = payload.get("command")
                if not project_id or not command:
                    self._json(400, {"error": "project_id and command required"})
                    return
                project = get_project(project_id)
                if not project:
                    self._json(404, {"error": "project not found"})
                    return
                policy = load_policy(Path(payload.get("policy", str(DEFAULT_POLICY))))
                reason = payload.get("reason")
                rec = create_request(
                    policy,
                    command,
                    Path(project["repo_path"]).resolve(),
                    reason=reason or f"project command: {project_id}",
                )
                rec["project_id"] = project_id
                self._json(200, rec)
                return

            if self.path == "/projects/add-task":
                project_id = payload.get("project_id")
                if not project_id:
                    self._json(400, {"error": "project_id required"})
                    return
                project = get_project(project_id)
                if not project:
                    self._json(404, {"error": "project not found"})
                    return
                task_type = payload.get("task_type", "unknown")
                objective = payload.get("objective", "")
                deadline = payload.get("deadline")
                extra_constraints = payload.get("constraints", "")
                constraints = json.dumps(
                    {
                        "project_id": project_id,
                        "repo_path": project["repo_path"],
                        "extra_constraints": extra_constraints,
                    },
                    ensure_ascii=True,
                )
                task_id = orchestrator.add_task(
                    source=f"project:{project_id}",
                    task_type=task_type,
                    objective=objective,
                    constraints=constraints,
                    deadline=deadline,
                )
                self._json(200, {"task_id": task_id, "project": project})
                return

            self._json(404, {"error": "not_found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})


def cmd_serve(args: argparse.Namespace) -> None:
    server = ThreadingHTTPServer((args.host, args.port), AgentAPIHandler)
    print(f"agent api listening on http://{args.host}:{args.port}")
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ai-chief agent entrypoint")
    sub = p.add_subparsers(required=True)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    p_prompts = sub.add_parser("show-prompts")
    p_prompts.set_defaults(func=cmd_show_prompts)

    p_gp = sub.add_parser("growth-plan")
    p_gp.add_argument("--window", type=int, default=200)
    p_gp.add_argument("--top-k", type=int, default=3)
    p_gp.set_defaults(func=cmd_growth_plan)

    p_gr = sub.add_parser("growth-request")
    p_gr.add_argument("--window", type=int, default=200)
    p_gr.add_argument("--top-k", type=int, default=3)
    p_gr.add_argument("--cwd", default=str(ROOT.parent))
    p_gr.add_argument("--policy", default=str(DEFAULT_POLICY))
    p_gr.set_defaults(func=cmd_growth_request)

    p_pd = sub.add_parser("projects-discover")
    p_pd.add_argument("--root", default=str(DEFAULT_ROOT))
    p_pd.add_argument("--max-depth", type=int, default=2)
    p_pd.set_defaults(func=cmd_projects_discover)

    p_pl = sub.add_parser("projects-list")
    p_pl.add_argument("--enabled-only", action="store_true")
    p_pl.set_defaults(func=cmd_projects_list)

    p_pcr = sub.add_parser("project-command-request")
    p_pcr.add_argument("--project-id", required=True)
    p_pcr.add_argument("--command", required=True)
    p_pcr.add_argument("--reason", default=None)
    p_pcr.add_argument("--policy", default=str(DEFAULT_POLICY))
    p_pcr.set_defaults(func=cmd_project_command_request)

    p_pat = sub.add_parser("project-add-task")
    p_pat.add_argument("--project-id", required=True)
    p_pat.add_argument("--task-type", required=True)
    p_pat.add_argument("--objective", required=True)
    p_pat.add_argument("--constraints", default="")
    p_pat.add_argument("--deadline", default=None)
    p_pat.set_defaults(func=cmd_project_add_task)

    p_run = sub.add_parser("run")
    p_run.add_argument("--inbox", default=None)
    p_run.add_argument("--limit", type=int, default=20)
    p_run.add_argument("--window", type=int, default=200)
    p_run.add_argument("--auto-activate", action="store_true")
    p_run.add_argument("--skip-ingest", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--path", required=True)
    p_ingest.add_argument("--default-source", default="manual")
    p_ingest.set_defaults(func=cmd_ingest)

    p_rq = sub.add_parser("run-queued")
    p_rq.add_argument("--limit", type=int, default=20)
    p_rq.set_defaults(func=cmd_run_queued)

    p_ev = sub.add_parser("evolve")
    p_ev.add_argument("--window", type=int, default=200)
    p_ev.add_argument("--to-version", default=None)
    p_ev.add_argument("--auto-activate", action="store_true")
    p_ev.set_defaults(func=cmd_evolve)

    p_cr = sub.add_parser("command-request")
    p_cr.add_argument("--command", required=True)
    p_cr.add_argument("--cwd", default=str(ROOT.parent))
    p_cr.add_argument("--reason", default=None)
    p_cr.add_argument("--policy", default=str(DEFAULT_POLICY))
    p_cr.set_defaults(func=cmd_command_request)

    p_ca = sub.add_parser("command-approve")
    p_ca.add_argument("--request-id", required=True)
    p_ca.add_argument("--approved-by", required=True)
    p_ca.add_argument("--reason", required=True)
    p_ca.add_argument("--execute", action="store_true")
    p_ca.add_argument("--policy", default=str(DEFAULT_POLICY))
    p_ca.set_defaults(func=cmd_command_approve)

    p_ce = sub.add_parser("command-exec")
    p_ce.add_argument("--command", required=True)
    p_ce.add_argument("--cwd", default=str(ROOT.parent))
    p_ce.add_argument("--reason", default=None)
    p_ce.add_argument("--approved-by", default=None)
    p_ce.add_argument("--approval-reason", default=None)
    p_ce.add_argument("--policy", default=str(DEFAULT_POLICY))
    p_ce.set_defaults(func=cmd_command_exec)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.set_defaults(func=cmd_serve)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
