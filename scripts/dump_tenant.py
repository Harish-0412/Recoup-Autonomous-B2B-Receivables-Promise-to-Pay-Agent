#!/usr/bin/env python3
"""Weekly logical dump of the public schema to S3/R2 (cross-platform shim).

Prefer the bash wrapper (dump_tenant.sh) on linux/mac. This Python
variant avoids git-bash on Windows while producing the same pg_dump
custom-format file and s3 upload.

Requires: pg_dump on PATH, boto3 installed, env vars:
    DATABASE_URL, BACKUP_BUCKET (default: recoup-backups),
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (and AWS_ENDPOINT_URL for R2).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERR: DATABASE_URL not set", file=sys.stderr)
        return 2
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        print("ERR: pg_dump not found on PATH", file=sys.stderr)
        return 2
    bucket = os.environ.get("BACKUP_BUCKET", "recoup-backups")
    ts = datetime.now(UTC).strftime("%Y%m%d")
    dump_path = f"/tmp/recoup_{ts}.dump" if os.name != "nt" else f"./recoup_{ts}.dump"
    key = f"weekly/{ts}/recoup_tenant.dump"
    subprocess.run(
        [pg_dump, "-d", db_url, "-n", "public", "-F", "c", "-f", dump_path, "--no-owner"],
        check=True,
    )
    try:
        import boto3
    except ImportError:
        print(
            f"OK: dumped locally to {dump_path}; boto3 missing — skipping S3 upload.",
            file=sys.stderr,
        )
        return 0
    s3 = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"))
    s3.upload_file(dump_path, bucket, key, ExtraArgs={"StorageClass": "STANDARD_IA"})
    os.remove(dump_path)
    print(f"[OK] dumped to s3://{bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
