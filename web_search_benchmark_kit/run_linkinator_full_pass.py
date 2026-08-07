#!/usr/bin/env python3
"""Run one bounded Linkinator pass over every normalized result URL."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path


def load_urls(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        urls = [row["url"].strip() for row in csv.DictReader(handle) if row.get("url", "").strip()]
    return list(dict.fromkeys(urls))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=20_000)
    parser.add_argument("--retry-errors-count", type=int, default=2)
    args = parser.parse_args()

    urls = load_urls(args.input_csv)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "npx",
        "--yes",
        "--package",
        "linkinator@8.0.3",
        "linkinator",
        *urls,
        "--format",
        "JSON",
        "--concurrency",
        str(args.concurrency),
        "--timeout",
        str(args.timeout),
        "--retry-errors",
        "--retry-errors-count",
        str(args.retry_errors_count),
        "--verbosity",
        "info",
    ]
    print(f"checking {len(urls)} unique URLs", flush=True)
    print(f"concurrency={args.concurrency} timeout_ms={args.timeout}", flush=True)
    with args.output_json.open("w", encoding="utf-8") as output, args.log.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=output, stderr=log, check=False)
    metadata = {
        "tool": "linkinator",
        "version": "8.0.3",
        "requested_url_count": len(urls),
        "returncode": completed.returncode,
        "concurrency": args.concurrency,
        "timeout_ms": args.timeout,
        "retry_errors_count": args.retry_errors_count,
    }
    metadata_path = args.output_json.with_suffix(".meta.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata), flush=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
