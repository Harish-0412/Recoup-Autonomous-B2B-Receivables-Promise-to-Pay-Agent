"""Manually trigger one autonomous batch run against a running API.

    python scripts/trigger_batch.py [--url http://127.0.0.1:8000] [--limit 50]

Reads TASK_API_KEY from the environment (never from argv, so it does not end
up in shell history or process lists). Prints the RunSummary JSON the endpoint
returns. The same call is what .github/workflows/scheduler.yml and the
render.yaml cron make every 15 minutes -- this script is the local form of
that cron, for smoke-testing a deploy before enabling the schedule.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger one autonomous batch run.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--limit", default=None, help="Override BATCH_MAX_INVOICES")
    args = parser.parse_args()

    key = os.environ.get("TASK_API_KEY", "")
    if not key:
        print("TASK_API_KEY is not set in the environment; refusing to guess.", file=sys.stderr)
        return 2

    target = args.url.rstrip("/") + "/api/v1/tasks/run-batch"
    if args.limit:
        target += f"?limit={args.limit}"
    req = urllib.request.Request(target, method="POST", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8", "replace")
            print(f"HTTP {resp.status}")
            try:
                print(json.dumps(json.loads(body), indent=2))
            except json.JSONDecodeError:
                print(body)
            return 0 if resp.status < 400 else 1
    except Exception as exc:  # network down, 401/503 -- print and exit non-zero
        print(f"Trigger failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
