---
name: command-control
description: 对本地 bash 命令进行风险判定、审批编排与审计记录。
---

# Command Control Skill

## 何时使用
- 需要执行 shell 命令时
- 需要审批高风险命令时

## 规则
- 先判定后执行
- require_approval 命令必须走 request -> approve
- 必须记录审计日志

## 输出
- decision
- request_id（如需审批）
- execution_result（若已执行）
