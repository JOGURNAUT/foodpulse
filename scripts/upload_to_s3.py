"""Upload the raw CSVs to S3, one prefix per table.

    python scripts/upload_to_s3.py --bucket my-foodpulse-lake
    python scripts/upload_to_s3.py --bucket my-foodpulse-lake --dry-run

Layout, which is what makes the Snowflake side simple:

    s3://<bucket>/raw/orders/orders.csv
    s3://<bucket>/raw/order_items/order_items.csv
    ...

A folder per table rather than seven files in one folder, because `COPY INTO`
points at a prefix and loads everything under it. With a folder per table you
can drop tomorrow's `orders-2026-10-10.csv` next to today's and the same COPY
statement picks it up; with one flat folder you would need a per-file pattern
and would load the restaurants into the orders table the day someone forgot one.

Credentials are never arguments. boto3 reads them from the standard chain --
environment variables, `~/.aws/credentials`, or an attached instance role -- so
they stay out of `ps`, out of shell history and out of this repo. A key passed
as `--aws-secret-key` is visible to every other process on the machine for as
long as the upload runs.

The same key is written every time, so re-running replaces rather than appends:
S3 has no append, and a retry that created `orders-1.csv` would be loaded as
extra orders by the next COPY.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLES = ("restaurants", "menu", "users", "orders", "order_items", "reviews")


class Progress:
    """boto3 calls this from its upload threads, so the counter needs a lock."""

    def __init__(self, name: str, total: int):
        self.name, self.total, self.seen = name, total, 0
        self._lock = threading.Lock()

    def __call__(self, chunk: int) -> None:
        with self._lock:
            self.seen += chunk
            pct = self.seen / self.total * 100 if self.total else 100
            sys.stdout.write(f"\r  {self.name:<14} {pct:5.1f}%")
            sys.stdout.flush()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    p.add_argument("--prefix", default="raw")
    p.add_argument("--data", default="data/raw")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be uploaded and exit")
    args = p.parse_args(argv)

    data = ROOT / args.data
    files = [(t, data / f"{t}.csv") for t in TABLES]
    missing = [t for t, path in files if not path.exists()]
    if missing:
        print(f"missing CSVs for: {', '.join(missing)}\n"
              f"run `python -m foodpulse.generate` first", file=sys.stderr)
        return 1

    total_mb = sum(path.stat().st_size for _, path in files) / 1024 / 1024
    print(f"s3://{args.bucket}/{args.prefix}/   ({total_mb:.0f} MB in {len(files)} files)")

    if args.dry_run:
        for table, path in files:
            mb = path.stat().st_size / 1024 / 1024
            print(f"  {table:<14} {mb:>8.1f} MB  ->  {args.prefix}/{table}/{path.name}")
        print("\ndry run, nothing uploaded")
        return 0

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        print("boto3 is not installed:  pip install boto3", file=sys.stderr)
        return 1

    s3 = boto3.client("s3")
    try:
        for table, path in files:
            key = f"{args.prefix}/{table}/{path.name}"
            # upload_file splits anything large into a multipart upload on its
            # own, and retries the parts rather than the whole file.
            s3.upload_file(
                str(path), args.bucket, key,
                ExtraArgs={"ContentType": "text/csv"},
                Callback=Progress(table, path.stat().st_size),
            )
            print(f"\r  {table:<14} 100.0%  ->  s3://{args.bucket}/{key}")
    except NoCredentialsError:
        print("\nno AWS credentials found.\n"
              "set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, or run `aws configure`",
              file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as exc:
        print(f"\nupload failed: {exc}", file=sys.stderr)
        return 1

    print(f"\n{len(files)} files in s3://{args.bucket}/{args.prefix}/")
    print("next: snowflake/02_storage_integration.sql, then 03, 04, 05")
    return 0


if __name__ == "__main__":
    sys.exit(main())
