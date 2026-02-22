#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "eval" / "benchmark_set.jsonl"


def run_score() -> dict:
    total = 0
    passed = 0
    with BENCH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            item = json.loads(line)
            required = set(item.get("expected", {}).get("must_include", []))
            # Placeholder pass logic; replace with real artifact checks.
            if required:
                passed += 1

    score = (passed / total) if total else 0.0
    return {"total": total, "passed": passed, "score": round(score, 4)}


def run() -> None:
    print(json.dumps(run_score()))


if __name__ == "__main__":
    run()
