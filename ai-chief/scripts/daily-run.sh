#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/zqs/Downloads/project/myself/ai-chief"
ORCH="${ROOT}/runtime/orchestrator.py"
INBOX_DEFAULT="${ROOT}/inbox/tasks.example.jsonl"
LOG_DIR="${ROOT}/logs"
mkdir -p "${LOG_DIR}"

INBOX_PATH="${INBOX_DEFAULT}"
RUN_LIMIT=20
WINDOW=200
AUTO_ACTIVATE=false
INGEST=true

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Options:
  --inbox PATH            JSONL task inbox path (default: ${INBOX_DEFAULT})
  --limit N               Max queued tasks to run (default: 20)
  --window N              Train-cycle sample window (default: 200)
  --auto-activate         Auto activate policy when approved
  --skip-ingest           Skip inbox ingestion
  -h, --help              Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inbox)
      INBOX_PATH="$2"
      shift 2
      ;;
    --limit)
      RUN_LIMIT="$2"
      shift 2
      ;;
    --window)
      WINDOW="$2"
      shift 2
      ;;
    --auto-activate)
      AUTO_ACTIVATE=true
      shift
      ;;
    --skip-ingest)
      INGEST=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
RUN_LOG="${LOG_DIR}/daily-run-${TS}.log"

log() {
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*" | tee -a "${RUN_LOG}"
}

run_cmd() {
  log "RUN: $*"
  "$@" 2>&1 | tee -a "${RUN_LOG}"
}

log "daily-run start"
run_cmd python3 "${ORCH}" init-db

if [[ "${INGEST}" == "true" ]]; then
  if [[ -f "${INBOX_PATH}" ]]; then
    run_cmd python3 "${ORCH}" ingest-inbox --path "${INBOX_PATH}"
  else
    log "inbox file not found, skipping ingestion: ${INBOX_PATH}"
  fi
else
  log "ingestion disabled by --skip-ingest"
fi

run_cmd python3 "${ORCH}" run-queued --limit "${RUN_LIMIT}"
run_cmd python3 "${ORCH}" metrics --limit "${WINDOW}"

if [[ "${AUTO_ACTIVATE}" == "true" ]]; then
  run_cmd python3 "${ORCH}" train-cycle --window "${WINDOW}" --auto-activate
else
  run_cmd python3 "${ORCH}" train-cycle --window "${WINDOW}"
fi

run_cmd python3 "${ORCH}" policy-state
run_cmd python3 "${ORCH}" list-policy-changes --limit 5

log "daily-run done"
log "log file: ${RUN_LOG}"
