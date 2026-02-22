---
name: find-skills
description: 用于发现并评估可增强 Agent 能力的 skills（本地与 GitHub）。
---

# Find Skills Skill

## 参考
- vercel-labs/skills find-skills 规范：
  - https://github.com/vercel-labs/skills/blob/main/skills/find-skills/SKILL.md

## 何时使用
- 出现重复失败模式，需要新能力补齐
- 需要新增领域技能（如安全审查、竞品研究、PoC自动化）

## 检索策略
1. 先本地检索已安装技能
2. 再检索 GitHub 开源技能
3. 评估可信度、维护活跃度、适配成本
4. 生成安装建议，安装动作走审批

## 输出
- candidate_skills
- fit_reason
- risk_note
- adoption_plan
