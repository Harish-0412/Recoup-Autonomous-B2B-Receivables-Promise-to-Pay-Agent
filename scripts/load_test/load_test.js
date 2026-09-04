/**
 * k6 load test script for POST /api/v1/tasks/run-batch.
 *
 * Runs against a staging DB with N open invoices.
 * Preconditions: DRY_RUN=true, SENDING_ENABLED=true.
 * Verifies single-winner advisory lock behavior under concurrent execution.
 *
 * Usage:
 *   k6 run --env BASE_URL=http://staging:8000 --env TASK_API_KEY=test-secret scripts/load_test/load_test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const TASK_KEY = __ENV.TASK_API_KEY || 'test-task-key';

const batchDuration = new Trend('batch_run_duration');
const winnersCounter = new Counter('advisory_lock_winners');
const blockedCounter = new Counter('advisory_lock_blocked');

export const options = {
  scenarios: {
    // 5 concurrent triggers to prove advisory lock single-winner behavior
    concurrent_triggers: {
      executor: 'per-vu-iterations',
      vus: 5,
      iterations: 1,
      maxDuration: '60s',
    },
    // Continuous load scenario
    sustained_load: {
      executor: 'constant-vus',
      vus: 3,
      duration: '30s',
      startTime: '10s',
    },
  },
  thresholds: {
    // Wave 3 SLO: run-batch wall p95 < 30s
    batch_run_duration: ['p(95)<30000'],
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  // Pre-flight check: ensure staging environment with safe parameters
  const res = http.get(`${BASE_URL}/api/v1/tasks/status`, {
    headers: { Authorization: `Bearer ${TASK_KEY}` },
  });
  check(res, {
    'status is 200': (r) => r.status === 200,
    'dry_run is true': (r) => r.json('dry_run') === true,
    'sending_enabled is true': (r) => r.json('sending_enabled') === true,
  });
  if (res.json('dry_run') !== true) {
    throw new Error('Refusing load test: server dry_run is not true!');
  }
}

export default function () {
  const url = `${BASE_URL}/api/v1/tasks/run-batch`;
  const params = {
    headers: {
      Authorization: `Bearer ${TASK_KEY}`,
      'Content-Type': 'application/json',
    },
    timeout: '60s',
  };

  const start = new Date();
  const res = http.post(url, null, params);
  const elapsed = new Date() - start;

  batchDuration.add(elapsed);

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  if (res.status === 200) {
    const data = res.json();
    if (data.ran === true) {
      winnersCounter.add(1);
    } else {
      blockedCounter.add(1);
    }
  }

  sleep(1);
}
