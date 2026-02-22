#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import yaml
except Exception as exc:  # pragma: no cover
    print(f"missing dependency: PyYAML ({exc})", file=sys.stderr)
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "security" / "command-policy.yaml"
REQUEST_STORE = ROOT / "memory" / "command_requests.json"


class GuardError(Exception):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_policy(path: Path) -> dict:
    if not path.exists():
        raise GuardError(f"policy not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GuardError("invalid policy format")
    return data


def path_under_allowed(cwd: Path, allowed: list[str]) -> bool:
    cwd_r = cwd.resolve()
    for base in allowed:
        try:
            base_r = Path(base).resolve()
            cwd_r.relative_to(base_r)
            return True
        except Exception:
            continue
    return False


def split_segments(raw_cmd: str) -> list[str]:
    return [raw_cmd.strip()] if raw_cmd.strip() else []


def starts_with(tokens: list[str], prefix: list[str]) -> bool:
    if len(tokens) < len(prefix):
        return False
    return tokens[: len(prefix)] == prefix


def find_matching_profile(policy: dict, tokens: list[str]) -> tuple[str | None, dict | None, dict | None]:
    profiles = policy.get("profiles", {})
    for profile_name, profile in profiles.items():
        for cmd_rule in profile.get("commands", []):
            prefix = cmd_rule.get("prefix", [])
            if not starts_with(tokens, prefix):
                continue

            regexes = cmd_rule.get("args_allow_regex", [])
            if regexes:
                rest = tokens[len(prefix) :]
                non_flag_args = [a for a in rest if not a.startswith("-")]
                if not non_flag_args:
                    continue
                matched = any(any(re.match(rgx, a) for rgx in regexes) for a in non_flag_args)
                if not matched:
                    continue

            return profile_name, profile, cmd_rule
    return None, None, None


def enforce_constraints(tokens: list[str], profile: dict | None, policy: dict, cwd: Path) -> tuple[bool, str | None]:
    if not profile:
        return True, None

    constraints = profile.get("constraints", {})
    if constraints.get("require_target_under_workdir"):
        allowed = policy.get("execution", {}).get("allowed_workdirs", [])
        if not allowed:
            return False, "no allowed_workdirs configured"
        if len(tokens) < 2:
            return False, "missing target path"
        target = Path(tokens[-1]).expanduser()
        if not target.is_absolute():
            target = (cwd / target).resolve()
        if not path_under_allowed(target, allowed):
            return False, f"target path outside allowed_workdirs: {target}"
    return True, None


def apply_risk_rules(raw_cmd: str, base_action: str, risk_rules: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []

    for rgx in risk_rules.get("deny_if_matches_regex", []):
        if re.search(rgx, raw_cmd):
            reasons.append(f"deny regex matched: {rgx}")
            return "deny", reasons

    action = base_action
    for token in risk_rules.get("require_approval_if_contains", []):
        if token in raw_cmd and action == "allow":
            reasons.append(f"approval required due to token: {token}")
            action = "require_approval"

    return action, reasons


def decide(policy: dict, raw_cmd: str, cwd: Path) -> dict:
    allowed_workdirs = policy.get("execution", {}).get("allowed_workdirs", [])
    if allowed_workdirs and not path_under_allowed(cwd, allowed_workdirs):
        return {
            "action": "deny",
            "profile": None,
            "reason": f"cwd outside allowed_workdirs: {cwd}",
            "matched_rule": None,
            "risk_reasons": [],
        }

    segments = split_segments(raw_cmd)
    if not segments:
        return {
            "action": "deny",
            "profile": None,
            "reason": "empty command",
            "matched_rule": None,
            "risk_reasons": [],
        }

    tokens = shlex.split(segments[0])
    profile_name, profile, cmd_rule = find_matching_profile(policy, tokens)

    if profile:
        ok, detail = enforce_constraints(tokens, profile, policy, cwd)
        if not ok:
            return {
                "action": "deny",
                "profile": profile_name,
                "reason": detail,
                "matched_rule": cmd_rule,
                "risk_reasons": [],
            }
        base_action = profile.get("action", policy.get("default_action", "require_approval"))
        base_reason = f"matched profile: {profile_name}"
    else:
        base_action = policy.get("default_action", "require_approval")
        base_reason = "no profile matched; fallback to default_action"

    final_action, risk_reasons = apply_risk_rules(raw_cmd, base_action, policy.get("risk_rules", {}))

    return {
        "action": final_action,
        "profile": profile_name,
        "reason": base_reason,
        "matched_rule": cmd_rule,
        "risk_reasons": risk_reasons,
    }


def redact(text: str, patterns: list[str]) -> str:
    out = text
    for p in patterns:
        out = re.sub(p, "[REDACTED]", out)
    return out


def write_audit(policy: dict, event: dict) -> None:
    sink = policy.get("audit", {}).get("sink")
    if not sink:
        return
    path = Path(sink)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")


def run_exec(tokens: list[str], cwd: Path, timeout_s: int) -> subprocess.CompletedProcess:
    return subprocess.run(tokens, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s, check=False)


def load_requests() -> dict:
    if not REQUEST_STORE.exists():
        return {}
    return json.loads(REQUEST_STORE.read_text(encoding="utf-8"))


def save_requests(data: dict) -> None:
    REQUEST_STORE.parent.mkdir(parents=True, exist_ok=True)
    REQUEST_STORE.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def execute_command(policy: dict, request: dict, approved_by: str | None = None, approval_reason: str | None = None) -> dict:
    cwd = Path(request["cwd"])
    command = request["command"]
    started = time.time()

    tokens = shlex.split(command)
    max_runtime = int(policy.get("execution", {}).get("max_runtime_seconds", 180))
    cp = run_exec(tokens, cwd, max_runtime)

    out_lim = int(policy.get("audit", {}).get("include_stdout_preview_chars", 600))
    err_lim = int(policy.get("audit", {}).get("include_stderr_preview_chars", 600))
    redact_patterns = policy.get("execution", {}).get("redact_patterns", [])

    result = {
        "status": "executed",
        "executed": True,
        "exit_code": cp.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "stdout_preview": redact((cp.stdout or "")[:out_lim], redact_patterns),
        "stderr_preview": redact((cp.stderr or "")[:err_lim], redact_patterns),
        "approved_by": approved_by,
        "approval_reason": approval_reason,
    }
    return result


def create_request(policy: dict, command: str, cwd: Path, reason: str | None = None) -> dict:
    decision_result = decide(policy, command, cwd)
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    status = "pending"
    if decision_result["action"] == "deny":
        status = "denied"
    elif decision_result["action"] == "allow":
        status = "approved"

    record = {
        "request_id": request_id,
        "command": command,
        "cwd": str(cwd),
        "decision": decision_result,
        "status": status,
        "created_at": now_iso(),
        "request_reason": reason,
        "approved_by": None,
        "approval_reason": None,
        "executed": False,
        "exit_code": None,
        "duration_ms": None,
        "stdout_preview": "",
        "stderr_preview": "",
    }

    requests = load_requests()
    requests[request_id] = record
    save_requests(requests)
    return record


def cmd_check(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    decision_result = decide(policy, args.command, Path(args.cwd).resolve())
    out = {
        "request_id": f"req-{uuid.uuid4().hex[:8]}",
        "command": args.command,
        "cwd": str(Path(args.cwd).resolve()),
        "decision": decision_result,
    }
    print(json.dumps(out, ensure_ascii=True))


def cmd_request(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    cwd = Path(args.cwd).resolve()
    rec = create_request(policy, args.command, cwd, reason=args.reason)

    event = {
        "ts": int(time.time()),
        "request_id": rec["request_id"],
        "command": rec["command"],
        "cwd": rec["cwd"],
        "decision": rec["decision"],
        "status": rec["status"],
        "event": "request_created",
    }
    write_audit(policy, event)

    if args.execute_if_allow and rec["decision"]["action"] == "allow":
        result = execute_command(policy, rec)
        requests = load_requests()
        requests[rec["request_id"]].update(result)
        requests[rec["request_id"]]["status"] = "executed"
        requests[rec["request_id"]]["executed_at"] = now_iso()
        save_requests(requests)

        event2 = {
            "ts": int(time.time()),
            "request_id": rec["request_id"],
            "command": rec["command"],
            "cwd": rec["cwd"],
            "decision": rec["decision"],
            **result,
            "event": "executed_auto_allow",
        }
        write_audit(policy, event2)

        print(json.dumps(requests[rec["request_id"]], ensure_ascii=True))
        return

    print(json.dumps(rec, ensure_ascii=True))


def cmd_approve(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    requests = load_requests()
    rec = requests.get(args.request_id)
    if not rec:
        raise GuardError(f"request not found: {args.request_id}")

    if rec["decision"]["action"] != "require_approval":
        raise GuardError(f"request does not require approval: {args.request_id}")

    if rec["status"] in ("executed", "denied"):
        raise GuardError(f"request already finalized: status={rec['status']}")

    rec["approved_by"] = args.approved_by
    rec["approval_reason"] = args.reason
    rec["status"] = "approved"
    rec["approved_at"] = now_iso()

    if args.execute:
        result = execute_command(policy, rec, approved_by=args.approved_by, approval_reason=args.reason)
        rec.update(result)
        rec["status"] = "executed"
        rec["executed_at"] = now_iso()

    requests[args.request_id] = rec
    save_requests(requests)

    event = {
        "ts": int(time.time()),
        "request_id": rec["request_id"],
        "command": rec["command"],
        "cwd": rec["cwd"],
        "decision": rec["decision"],
        "status": rec["status"],
        "approved_by": rec.get("approved_by"),
        "approval_reason": rec.get("approval_reason"),
        "executed": rec.get("executed"),
        "exit_code": rec.get("exit_code"),
        "duration_ms": rec.get("duration_ms"),
        "stdout_preview": rec.get("stdout_preview", ""),
        "stderr_preview": rec.get("stderr_preview", ""),
        "event": "approved",
    }
    write_audit(policy, event)

    print(json.dumps(rec, ensure_ascii=True))


def cmd_list_requests(args: argparse.Namespace) -> None:
    requests = load_requests()
    items = list(requests.values())
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if args.status:
        items = [x for x in items if x.get("status") == args.status]
    if args.limit:
        items = items[: args.limit]
    print(json.dumps({"count": len(items), "requests": items}, ensure_ascii=True))


def cmd_exec(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    cwd = Path(args.cwd).resolve()

    rec = create_request(policy, args.command, cwd, reason=args.request_reason)
    decision_action = rec["decision"]["action"]

    if decision_action == "deny":
        event = {
            "ts": int(time.time()),
            "request_id": rec["request_id"],
            "command": rec["command"],
            "cwd": rec["cwd"],
            "decision": rec["decision"],
            "status": "blocked",
            "event": "exec_blocked",
        }
        write_audit(policy, event)
        print(json.dumps(rec, ensure_ascii=True))
        return

    if decision_action == "require_approval" and not (args.approved_by and args.approval_reason):
        rec["status"] = "pending"
        requests = load_requests()
        requests[rec["request_id"]] = rec
        save_requests(requests)

        event = {
            "ts": int(time.time()),
            "request_id": rec["request_id"],
            "command": rec["command"],
            "cwd": rec["cwd"],
            "decision": rec["decision"],
            "status": "awaiting_approval",
            "event": "exec_pending_approval",
        }
        write_audit(policy, event)
        print(json.dumps(rec, ensure_ascii=True))
        return

    if decision_action == "require_approval":
        rec["approved_by"] = args.approved_by
        rec["approval_reason"] = args.approval_reason

    result = execute_command(policy, rec, approved_by=args.approved_by, approval_reason=args.approval_reason)
    rec.update(result)
    rec["status"] = "executed"
    rec["executed_at"] = now_iso()

    requests = load_requests()
    requests[rec["request_id"]] = rec
    save_requests(requests)

    event = {
        "ts": int(time.time()),
        "request_id": rec["request_id"],
        "command": rec["command"],
        "cwd": rec["cwd"],
        "decision": rec["decision"],
        **result,
        "event": "exec_completed",
    }
    write_audit(policy, event)

    print(json.dumps(rec, ensure_ascii=True))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Policy-based command guard")
    p.add_argument("--policy", default=str(DEFAULT_POLICY))

    sub = p.add_subparsers(required=True)

    p_check = sub.add_parser("check")
    p_check.add_argument("--command", required=True)
    p_check.add_argument("--cwd", default=os.getcwd())
    p_check.set_defaults(func=cmd_check)

    p_request = sub.add_parser("request")
    p_request.add_argument("--command", required=True)
    p_request.add_argument("--cwd", default=os.getcwd())
    p_request.add_argument("--reason", default=None)
    p_request.add_argument("--execute-if-allow", action="store_true")
    p_request.set_defaults(func=cmd_request)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("--request-id", required=True)
    p_approve.add_argument("--approved-by", required=True)
    p_approve.add_argument("--reason", required=True)
    p_approve.add_argument("--execute", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_lr = sub.add_parser("list-requests")
    p_lr.add_argument("--status", default=None)
    p_lr.add_argument("--limit", type=int, default=50)
    p_lr.set_defaults(func=cmd_list_requests)

    p_exec = sub.add_parser("exec")
    p_exec.add_argument("--command", required=True)
    p_exec.add_argument("--cwd", default=os.getcwd())
    p_exec.add_argument("--request-reason", default=None)
    p_exec.add_argument("--approved-by", default=None)
    p_exec.add_argument("--approval-reason", default=None)
    p_exec.set_defaults(func=cmd_exec)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except GuardError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        sys.exit(1)


if __name__ == "__main__":
    main()
