from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_REGISTRY = ROOT / "configs" / "projects.json"
DEFAULT_ROOT = Path("/Users/zqs/Downloads/project")


def _slug(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "project"


def _repo_default_commands(path: Path) -> dict:
    cmds = []
    if (path / "package.json").exists():
        cmds.append({"name": "test", "cmd": "npm test"})
        cmds.append({"name": "lint", "cmd": "npm run lint"})
    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists():
        cmds.append({"name": "test", "cmd": "pytest"})
    if (path / "go.mod").exists():
        cmds.append({"name": "test", "cmd": "go test ./..."})
    if (path / "Cargo.toml").exists():
        cmds.append({"name": "test", "cmd": "cargo test"})
    return {"recommended_commands": cmds}


def _collect_candidate_repos(root: Path, max_depth: int) -> list[Path]:
    repos: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        if (p / ".git").exists():
            repos.append(p.resolve())
            continue
        if max_depth > 1:
            for n in sorted(p.glob("*/.git")):
                repos.append(n.parent.resolve())
    return repos


def _assign_unique_ids(items: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    out = []
    for item in items:
        base = item["project_id"]
        idx = seen.get(base, 0)
        if idx == 0 and base not in seen:
            item["project_id"] = base
            seen[base] = 1
        else:
            seen[base] = idx + 1
            item["project_id"] = f"{base}-{seen[base]}"
        out.append(item)
    return out


def discover_projects(root: Path = DEFAULT_ROOT, max_depth: int = 2) -> list[dict]:
    root = root.resolve()
    candidates = _collect_candidate_repos(root, max_depth=max_depth)

    uniq_paths = sorted({str(p): p for p in candidates}.values(), key=lambda x: x.name.lower())
    items: list[dict] = []
    for p in uniq_paths:
        items.append(
            {
                "project_id": _slug(p.name),
                "name": p.name,
                "repo_path": str(p),
                "enabled": True,
                "priority": 50,
                "profile": _repo_default_commands(p),
            }
        )

    return _assign_unique_ids(items)


def save_registry(projects: list[dict], root: Path = DEFAULT_ROOT) -> dict:
    data = {
        "root_path": str(root.resolve()),
        "project_count": len(projects),
        "projects": projects,
    }
    PROJECTS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_REGISTRY.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    return data


def load_registry() -> dict:
    if not PROJECTS_REGISTRY.exists():
        return {"root_path": str(DEFAULT_ROOT), "project_count": 0, "projects": []}
    return json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8"))


def get_project(project_id: str) -> dict | None:
    reg = load_registry()
    for p in reg.get("projects", []):
        if p.get("project_id") == project_id:
            return p
    return None
