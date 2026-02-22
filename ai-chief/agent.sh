#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/zqs/Downloads/project/myself/ai-chief"
exec python3 "${ROOT}/runtime/agent.py" "$@"
