---
name: training
description: 从 episodes 与 feedback 提炼策略更新提案，用于持续优化 Agent 行为。
---

# Training Skill

## 何时使用
- 周期性策略改进
- 返工率上升或升级率异常

## 输出模板
# Change Proposal
## Hypothesis
## Proposed Diff
## Expected Metric Impact
## Risk of Regression
## Rollout Plan (offline -> canary -> full)

## 规则
- 变更必须可评估、可回滚
- 优先小步快跑，先离线后灰度
