"""Staging load test for POST /api/v1/tasks/run-batch.

    python scripts/load_test/run_batch_load.py --base-url http://staging:8000 \
        --api-key $STAGING_API_KEY --task-key $STAGING_TASK_KEY \
        --sizes 50 100 200 400 800 --repeats 3

STAGING ONLY. The script refuses to run against anything that looks like
production (APP_ENV=production server, or a host matching --prod-markers)
because a load test hammers run-batch and seeds hundreds of tagged invoices.
Never point this at production webhooks: seeding and triggering are the only
calls it makes, but the volume alone is a self-inflicted incident there.

Server preconditions, asserted before any load:
  DRY_RUN=true (nothing is really sent) and SENDING_ENABLED=true, read from
  GET /api/v1/tasks/status. Razorpay/Resend run against their test doubles in
  DRY_RUN, so provider latency here is the mock path, stated as such.

What it measures per book size N (all with DRY_RUN=true):
  run-batch wall time, per-invoice p95 across repeats, invoices_considered.
Then a single-winner check: 5 concurrent triggers must yield exactly one
``ran=true`` (advisory lock), the rest ``ran=false`` no-ops.

Output: JSON to --out plus a markdown table on stdout for docs/load_test.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

TAG = "LT"
DEFAULT_PROD_MARKERS = ("prod", "api.recoup", "recoup.in")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--task-key", required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 100, 200, 400, 800])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed-batch", type=int, default=100)
    parser.add_argument("--out", type=Path, default=Path("data/load_test/results.json"))
    parser.add_argument("--prod-markers", nargs="*", default=list(DEFAULT_PROD_MARKERS))
    parser.add_argument("--i-am-sure-staging", action="store_true")
    return parser


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _assert_staging(
    client: httpx.AsyncClient, task_key: str, prod_markers: list[str]
) -> dict:
    response = await client.get("/api/v1/tasks/status", headers=_headers(task_key))
    response.raise_for_status()
    status = response.json()
    host = str(client.base_url)
    if any(m in host for m in prod_markers):
        raise SystemExit(
            f"refusing: {host} looks like production "
            "(pass --i-am-sure-staging only if you truly mean it)"
        )
    if status.get("dry_run") is not True:
        raise SystemExit(f"refusing: server DRY_RUN is not true: {status!r}")
    if status.get("sending_enabled") is not True:
        raise SystemExit(f"refusing: server SENDING_ENABLED is not true: {status!r}")
    return status


async def _seed(client: httpx.AsyncClient, api_key: str, total: int, batch: int) -> None:
    """Seed ``total`` open invoices (10 per customer) tagged LT-*, idempotently."""
    today = date.today()
    made = 0
    while made < total:
        chunk = min(batch, total - made)
        customers, invoices = [], []
        for i in range(made, made + chunk):
            cust = i // 10
            customers.append(
                {
                    "customer_id": f"{TAG}-C-{cust:05d}",
                    "name": f"Load Test Customer {cust}",
                    "email": f"load-{cust}@example.test",
                    "on_time_ratio_90d": 0.5,
                    "on_time_ratio_all_time": 0.6,
                    "avg_days_late": 12.0,
                    "invoice_count": 10,
                    "avg_invoice_amount": 50000.0,
                }
            )
            invoices.append(
                {
                    "invoice_id": f"{TAG}-INV-{i:06d}",
                    "customer_id": f"{TAG}-C-{cust:05d}",
                    "amount": 50000.0,
                    "issue_date": (today - timedelta(days=60)).isoformat(),
                    "due_date": (today - timedelta(days=20)).isoformat(),
                }
            )
        # Dedupe customers already seeded by earlier chunks in this run.
        seen, uniq_customers = set(), []
        for customer in customers:
            if customer["customer_id"] not in seen:
                seen.add(customer["customer_id"])
                uniq_customers.append(customer)
        response = await client.post(
            "/api/v1/invoices/batch",
            headers=_headers(api_key),
            json={"customers": uniq_customers, "invoices": invoices},
            timeout=120.0,
        )
        response.raise_for_status()
        made += chunk
    print(f"seeded {total} load-test invoices")


async def _timed_run(client: httpx.AsyncClient, task_key: str, limit: int | None = None) -> dict:
    started = time.perf_counter()
    url = "/api/v1/tasks/run-batch" + (f"?limit={limit}" if limit else "")
    response = await client.post(url, headers=_headers(task_key), timeout=600.0)
    wall = time.perf_counter() - started
    response.raise_for_status()
    summary = response.json()
    considered = summary.get("invoices_considered", 0) or 0
    return {
        "wall_s": round(wall, 3),
        "ran": summary.get("ran"),
        "considered": considered,
        "per_invoice_ms": round(wall / max(considered, 1) * 1000.0, 2),
        "acted": summary.get("acted"),
        "scored": summary.get("scored"),
    }


async def _single_winner_check(client: httpx.AsyncClient, task_key: str) -> dict:
    """Fire 5 concurrent triggers; exactly one may report ran=true."""

    async def _fire() -> dict:
        response = await client.post(
            "/api/v1/tasks/run-batch", headers=_headers(task_key), timeout=600.0
        )
        response.raise_for_status()
        return response.json()

    # A quiet book settles instantly, which can serialize the five and hide
    # contention -- so seed one fresh invoice to guarantee real work.
    await _seed(client, task_key, 1, 1)
    results = await asyncio.gather(*(_fire() for _ in range(5)))
    winners = sum(1 for result in results if result.get("ran") is True)
    return {"fired": 5, "winners": winners, "single_winner": winners == 1}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(int(pct / 100.0 * len(ordered)), len(ordered) - 1)
    return ordered[rank]


async def main_async(args: argparse.Namespace) -> dict:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        prestatus = await _assert_staging(client, args.task_key, args.prod_markers)
        print(f"target ok: business={prestatus.get('business_id')} dry_run=True sending=True")

        await _seed(client, args.api_key, max(args.sizes), args.seed_batch)
        # Warmup (JIT, caches, connection pool) -- not measured.
        warmup = await _timed_run(client, args.task_key)
        print(f"warmup: {warmup['wall_s']}s for {warmup['considered']} invoices")

        rows = []
        for size in args.sizes:
            runs = [
                await _timed_run(client, args.task_key, limit=size) for _ in range(args.repeats)
            ]
            walls = [run["wall_s"] for run in runs]
            per_invoice = [run["per_invoice_ms"] for run in runs]
            rows.append(
                {
                    "n": size,
                    "repeats": args.repeats,
                    "wall_mean_s": round(statistics.mean(walls), 3),
                    "wall_p95_s": round(_percentile(walls, 95), 3),
                    "per_invoice_p95_ms": round(_percentile(per_invoice, 95), 2),
                    "considered": runs[0]["considered"],
                }
            )
            print(
                f"N={size}: wall p95 {rows[-1]['wall_p95_s']}s, "
                f"per-invoice p95 {rows[-1]['per_invoice_p95_ms']}ms"
            )

        contention = await _single_winner_check(client, args.task_key)
        print(f"concurrency: {contention['winners']} winner(s) of 5")

        report = {
            "base_url": args.base_url,
            "dry_run": True,
            "sending_enabled": True,
            "sizes": rows,
            "concurrency": contention,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
        return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if any(m in args.base_url for m in args.prod_markers) and not args.i_am_sure_staging:
        raise SystemExit("refusing: base-url looks like production")
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
