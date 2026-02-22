from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_ROOT = ROOT / "prompts"
SYSTEM_PROMPT = PROMPTS_ROOT / "system.md"
SKILLS_ROOT = ROOT / "skills"


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"')
    return meta, body


def list_skill_manifest() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not SKILLS_ROOT.exists():
        return out

    for p in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        raw = read_text(p)
        meta, body = parse_frontmatter(raw)
        skill_name = meta.get("name", p.parent.name)
        out[skill_name] = {
            "path": str(p),
            "description": meta.get("description", ""),
            "title": _extract_title(body),
        }
    return out


def _extract_title(body: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def prompt_manifest() -> dict:
    skills = list_skill_manifest()
    return {
        "system_prompt_path": str(SYSTEM_PROMPT),
        "system_prompt_exists": SYSTEM_PROMPT.exists(),
        "skill_prompt_count": len(skills),
        "skills": skills,
    }


def load_system_prompt() -> str:
    return read_text(SYSTEM_PROMPT)


def load_skill_prompt(skill: str) -> str:
    path = SKILLS_ROOT / skill / "SKILL.md"
    return read_text(path)


def compose_prompt(skill: str) -> str:
    system = load_system_prompt().strip()
    skill_text = load_skill_prompt(skill).strip()
    if not system:
        return skill_text
    if not skill_text:
        return system
    return f"[SYSTEM]\n{system}\n\n[SKILL]\n{skill_text}\n"
