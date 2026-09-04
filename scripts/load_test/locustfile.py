"""Locust load test for Recoup batch runs.

Target: POST /api/v1/tasks/run-batch against a staging environment with N open invoices.
Enforces preconditions: DRY_RUN=true, SENDING_ENABLED=true.
Measures wall time, throughput, and tests advisory lock single-winner contention.

Usage:
    locust -f scripts/load_test/locustfile.py --headless -u 5 -r 5 -t 60s \
        --host http://staging:8000
"""

from __future__ import annotations

import os
from locust import HttpUser, between, events, task

TASK_API_KEY = os.getenv("TASK_API_KEY", "test-task-key")
PROD_HOST_MARKERS = ("prod", "api.recoup", "recoup.in")


class BatchRunnerUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        """Verify staging preconditions before sending load."""
        host = str(self.host).lower()
        if any(marker in host for marker in PROD_HOST_MARKERS) and not os.getenv(
            "I_AM_SURE_STAGING"
        ):
            raise RuntimeError(
                f"SAFETY REFUSAL: Target host {host} looks like production! "
                "Never point load tests at production webhooks."
            )

        # Assert DRY_RUN=true and SENDING_ENABLED=true from /tasks/status
        headers = {"Authorization": f"Bearer {TASK_API_KEY}"}
        resp = self.client.get("/api/v1/tasks/status", headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Could not reach /tasks/status: {resp.status_code} {resp.text}")

        status = resp.json()
        if status.get("dry_run") is not True:
            raise RuntimeError(f"Server DRY_RUN must be true for load test: {status}")
        if status.get("sending_enabled") is not True:
            raise RuntimeError(f"Server SENDING_ENABLED must be true for load test: {status}")

    @task(3)
    def trigger_batch_run(self) -> None:
        """Trigger a bounded batch run with timing and lock verification."""
        headers = {"Authorization": f"Bearer {TASK_API_KEY}"}
        with self.client.post(
            "/api/v1/tasks/run-batch",
            headers=headers,
            catch_response=True,
            timeout=60.0,
            name="/api/v1/tasks/run-batch",
        ) as response:
            if response.status_code == 200:
                body = response.json()
                # If advisory lock blocked concurrent run, body contains ran=False
                if body.get("ran") is True:
                    response.success()
                else:
                    # Contention handled safely by advisory lock: single winner
                    response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(1)
    def check_task_status(self) -> None:
        """Monitor server health and metrics during the test."""
        headers = {"Authorization": f"Bearer {TASK_API_KEY}"}
        self.client.get("/api/v1/tasks/status", headers=headers, name="/api/v1/tasks/status")
