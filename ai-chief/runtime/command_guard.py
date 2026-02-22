#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


class GuardError(Exception):
    pass


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
    # Lightweight splitter; policy already treats shell control ops as risky.
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


def enforce_constraints(tokens: list[str], profile: dict | None, cmd_rule: dict | None, policy: dict, cwd: Path) -> tuple[bool, str | None]:
    if not profile:
        return True, None

    constraints = profile.get("constraints", {})
    if constraints.get("require_target_under_workdir"):
        allowed = policy.get("execution", {}).get("allowed_workdirs", [])
        if not allowed:
            return False, "no allowed_workdirs configured"
        if len(tokens) < 2:
            return False, "missing target path"
        # Use last token as target path for clone operations.
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
        ok, detail = enforce_constraints(tokens, profile, cmd_rule, policy, cwd)
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


def cmd_exec(args: argparse.Namespace) -> None:
    policy = load_policy(Path(args.policy))
    cwd = Path(args.cwd).resolve()
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    started = time.time()

    decision_result = decide(policy, args.command, cwd)
    action = decision_result["action"]
    tokens = shlex.split(args.command)

    approved = bool(args.approved_by and args.reason)
    executed = False
    exit_code = None
    stdout_preview = ""
    stderr_preview = ""

    if action == "deny":
        status = "blocked"
    elif action == "require_approval" and not approved:
        status = "awaiting_approval"
    else:
        max_runtime = int(policy.get("execution", {}).get("max_runtime_seconds", 180))
        cp = run_exec(tokens, cwd, max_runtime)
        executed = True
        exit_code = cp.returncode

        out_lim = int(policy.get("audit", {}).get("include_stdout_preview_chars", 600))
        err_lim = int(policy.get("audit", {}).get("include_stderr_preview_chars", 600))
        redact_patterns = policy.get("execution", {}).get("redact_patterns", [])

        stdout_preview = redact((cp.stdout or "")[:out_lim], redact_patterns)
        stderr_preview = redact((cp.stderr or "")[:err_lim], redact_patterns)
        status = "executed"

    duration_ms = int((time.time() - started) * 1000)

    audit_event = {
        "ts": int(time.time()),
        "request_id": request_id,
        "command": args.command,
        "cwd": str(cwd),
        "decision": decision_result,
        "status": status,
        "approved_by": args.approved_by,
        "approval_reason": args.reason,
        "executed": executed,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
    }
    write_audit(policy, audit_event)

    print(json.dumps(audit_event, ensure_ascii=True))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Policy-based command guard")
    p.add_argument("--policy", default=str(DEFAULT_POLICY))

    sub = p.add_subparsers(required=True)

    p_check = sub.add_parser("check")
    p_check.add_argument("--command", required=True)
    p_check.add_argument("--cwd", default=os.getcwd())
    p_check.set_defaults(func=cmd_check)

    p_exec = sub.add_parser("exec")
    p_exec.add_argument("--command", required=True)
    p_exec.add_argument("--cwd", default=os.getcwd())
    p_exec.add_argument("--approved-by", default=None)
    p_exec.add_argument("--reason", default=None)
    p_exec.set_defaults(func=cmd_exec)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
