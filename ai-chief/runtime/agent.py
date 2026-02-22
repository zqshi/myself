#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import orchestrator
from command_guard import create_request, load_policy, load_requests, save_requests, execute_command, decide, DEFAULT_POLICY

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
