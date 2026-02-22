from __future__ import annotations

import json
import shlex
from pathlib import Path

AI_TOOLS_PATH = Path('/Users/zqs/Downloads/project/myself/ai-chief/configs/ai-tools.json')


def load_ai_tools() -> dict:
    if not AI_TOOLS_PATH.exists():
        return {
            "default_tool": "generic",
            "tools": {
                "generic": {
                    "description": "User provides full command manually",
                    "template": "{custom_command}",
                },
                "codex": {
                    "description": "Codex CLI style command template",
                    "template": "codex exec --cwd {repo_path} --prompt {prompt}",
                },
                "claude": {
                    "description": "Claude CLI style command template",
                    "template": "claude -p {prompt}",
                },
            },
        }
    return json.loads(AI_TOOLS_PATH.read_text(encoding="utf-8"))


def list_ai_tools() -> dict:
    cfg = load_ai_tools()
    tools = cfg.get("tools", {})
    return {
        "config_path": str(AI_TOOLS_PATH),
        "default_tool": cfg.get("default_tool", "generic"),
        "tools": {
            name: {
                "description": meta.get("description", ""),
                "template": meta.get("template", ""),
            }
            for name, meta in tools.items()
        },
    }


def render_ai_prompt(project_name: str, repo_path: str, objective: str, acceptance: str, constraints: str) -> str:
    return (
        f"你是项目执行工程师。项目: {project_name}; 路径: {repo_path}.\n"
        f"任务目标: {objective}\n"
        f"验收标准: {acceptance}\n"
        f"约束: {constraints or '遵守仓库现有规范，不做无关重构。'}\n"
        "要求: 直接修改代码并完成任务；完成后输出变更文件清单、关键实现说明、测试命令与结果。"
    )


def build_ai_command(
    tool_name: str,
    project_name: str,
    repo_path: str,
    objective: str,
    acceptance: str,
    constraints: str,
    custom_command: str | None = None,
) -> str:
    tools = load_ai_tools()
    mapping = tools.get("tools", {})
    chosen = mapping.get(tool_name) or mapping.get(tools.get("default_tool", "generic"), {})
    template = chosen.get("template", "{custom_command}")

    prompt = render_ai_prompt(project_name, repo_path, objective, acceptance, constraints)
    values = {
        "project_name": shlex.quote(project_name),
        "repo_path": shlex.quote(repo_path),
        "prompt": shlex.quote(prompt),
        "objective": shlex.quote(objective),
        "acceptance": shlex.quote(acceptance),
        "constraints": shlex.quote(constraints or ""),
        "custom_command": custom_command or "",
    }

    cmd = template.format(**values).strip()
    if tool_name == "generic" and custom_command:
        cmd = custom_command
    return cmd


def build_verification_commands(project: dict) -> list[str]:
    out = []
    profile = project.get("profile", {})
    for item in profile.get("recommended_commands", []):
        cmd = item.get("cmd")
        if cmd:
            out.append(cmd)
    return out


def build_execution_plan(
    project: dict,
    objective: str,
    acceptance: str,
    constraints: str,
    tool_name: str,
    custom_ai_command: str | None = None,
) -> dict:
    ai_cmd = build_ai_command(
        tool_name=tool_name,
        project_name=project.get("name", ""),
        repo_path=project.get("repo_path", ""),
        objective=objective,
        acceptance=acceptance,
        constraints=constraints,
        custom_command=custom_ai_command,
    )
    checks = build_verification_commands(project)

    steps = [
        {
            "step": "ai_execute",
            "command": ai_cmd,
            "reason": f"AI coding execution for project {project.get('name')}",
        }
    ]

    for c in checks:
        steps.append(
            {
                "step": "verify",
                "command": c,
                "reason": f"Post-change verification for {project.get('name')}",
            }
        )

    return {
        "project_id": project.get("project_id"),
        "project_name": project.get("name"),
        "repo_path": project.get("repo_path"),
        "objective": objective,
        "acceptance": acceptance,
        "constraints": constraints,
        "tool": tool_name,
        "steps": steps,
    }
