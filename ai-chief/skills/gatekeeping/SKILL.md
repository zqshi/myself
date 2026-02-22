---
name: gatekeeping
description: 基于阈值与评估结果决定策略变更的发布、拒绝或回滚。
---

# Gatekeeping Skill

## 何时使用
- 策略变更候选发布前
- 灰度观察期做放行/回滚判断

## 输出模板
# Release Decision
## Decision (approve/reject/rollback)
## Reason
## Metric Comparison
## Next Step

## 规则
- 不达阈值禁止发布
- 指标劣化触发回滚
