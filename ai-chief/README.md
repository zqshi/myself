# ai-chief (Self-Evolving Agent)

A runnable scaffold for a self-improving PM agent loop:
- Task ingestion from manual/inbox feeds
- Execution + critique + persisted episodes/feedback
- Metrics by active policy version
- Trainer/Gatekeeper release decision cycle
- Policy version activation and rollback-ready history

## Quick start

```bash
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py init-db
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py policy-state
```

## Task operations

```bash
# add one task manually
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py add-task \
  --task-type tech_scan \
  --objective "Summarize this week's key AI releases" \
  --constraints "No external commitments"

# ingest daily tasks from external systems dump
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py ingest-inbox \
  --path /Users/zqs/Downloads/project/myself/ai-chief/inbox/tasks.example.jsonl

python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py list-tasks --status queued
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py run-queued --limit 10
```

## Metrics and evolution

```bash
# overall metrics
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py metrics --limit 200

# metrics for active policy
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py metrics --policy-version v0.1.0 --limit 200

# propose/evaluate a new policy version
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py train-cycle --window 200

# propose/evaluate and auto-activate if approved
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py train-cycle --window 200 --auto-activate

# inspect policy change history
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py list-policy-changes

# manual activate/rollback
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/orchestrator.py activate-policy --version v0.1.0
```

## Notes
- Current `doer/critic/trainer/gatekeeper` are deterministic placeholders.
- Replace these modules with your real AI/toolchain integrations.
- Keep `memory/constitution.md` and `configs/guardrails.yaml` as your hard constraints.

## Command guard

Use policy-driven command execution for your digital twin:

```bash
# 1) dry-run policy decision
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/command_guard.py check \
  --command "gh search repos \"agent framework language:python stars:>200\" --limit 20" \
  --cwd /Users/zqs/Downloads/project/myself

# 2) execute if allow
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/command_guard.py exec \
  --command "rg --files /Users/zqs/Downloads/project/myself/ai-chief" \
  --cwd /Users/zqs/Downloads/project/myself

# 3) execute a guarded command with explicit approval fields
python3 /Users/zqs/Downloads/project/myself/ai-chief/runtime/command_guard.py exec \
  --command "git add -A" \
  --cwd /Users/zqs/Downloads/project/myself \
  --approved-by "zqs" \
  --reason "prepare checkpoint commit"
```

Policy file:
- `/Users/zqs/Downloads/project/myself/ai-chief/security/command-policy.yaml`

Audit log:
- `/Users/zqs/Downloads/project/myself/ai-chief/logs/command-audit.jsonl`

## Daily run

One-command daily pipeline:

```bash
# preview options
bash /Users/zqs/Downloads/project/myself/ai-chief/scripts/daily-run.sh --help

# run full loop with ingestion + evaluation
bash /Users/zqs/Downloads/project/myself/ai-chief/scripts/daily-run.sh --auto-activate

# run without ingestion (for rerun/debug)
bash /Users/zqs/Downloads/project/myself/ai-chief/scripts/daily-run.sh --skip-ingest --limit 10 --window 100
```

Daily run log file pattern:
- `/Users/zqs/Downloads/project/myself/ai-chief/logs/daily-run-<UTC timestamp>.log`
