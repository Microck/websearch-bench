#!/usr/bin/env python3
"""Run bounded normal HTTP GET checks through Tailscale SSH peers.

The local process sends only the URL list and receives status, redirect, body
size, title, and error metadata. It never transfers cookies or page bodies.
The remote command uses curl with normal certificate verification and HTTP/1.1.
"""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


OUTPUT_FIELDS = [
    "original_url",
    "prior_classification",
    "remote_host",
    "remote_hostname",
    "remote_os",
    "http_status",
    "final_url",
    "content_type",
    "body_bytes",
    "page_title",
    "redirects",
    "total_ms",
    "curl_exit",
    "curl_error",
    "body_valid",
    "classification",
    "observed_at",
]

REMOTE_CODE = r'''
import concurrent.futures
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

PAYLOAD = json.loads(sys.stdin.read())
HOST_LABEL = PAYLOAD["host"]
URLS = PAYLOAD["urls"]
WORKERS = int(PAYLOAD.get("workers", 6))
TIMEOUT = int(PAYLOAD.get("timeout", 20))
UA = "web-search-benchmark/1.0 (remote URL health check)"
TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
BLOCK_RE = re.compile(rb"just a moment|access denied|cloudflare|cf[-_]?chl|__cf_chl|verify you are human|security check|rate limit|too many requests|captcha|vercel security checkpoint", re.I)

def observed_at():
    return datetime.now(timezone.utc).isoformat()

def title_from(body):
    match = TITLE_RE.search(body[:65536])
    if not match:
        return ""
    return re.sub(rb"\s+", b" ", match.group(1)).decode("utf-8", "replace").strip()[:500]

def last_status(headers):
    statuses = re.findall(rb"^HTTP/\S+\s+(\d{3})", headers, re.M)
    return int(statuses[-1]) if statuses else 0

def run_one(item):
    url = item["url"]
    with tempfile.TemporaryDirectory(prefix="ws-health-") as directory:
        header_path = os.path.join(directory, "headers")
        body_path = os.path.join(directory, "body")
        command = [
            "curl", "-L", "--compressed", "--http1.1", "--silent", "--show-error",
            "--connect-timeout", str(min(8, TIMEOUT)), "--max-time", str(TIMEOUT),
            "--user-agent", UA, "--dump-header", header_path, "--output", body_path,
            "--write-out", "__META__\t%{url_effective}\t%{content_type}\t%{size_download}\t%{time_total}\t%{num_redirects}",
            url,
        ]
        started = time.monotonic()
        process = subprocess.run(command, capture_output=True, text=True, errors="replace")
        elapsed = time.monotonic() - started
        headers = b""
        body = b""
        try:
            with open(header_path, "rb") as handle:
                headers = handle.read(65536)
        except OSError:
            pass
        try:
            with open(body_path, "rb") as handle:
                body = handle.read(65536)
            body_size = os.path.getsize(body_path)
        except OSError:
            body_size = 0
        meta = process.stdout.split("__META__\t", 1)
        final_url = ""
        content_type = ""
        size_download = str(body_size)
        total_ms = f"{elapsed * 1000:.0f}"
        redirects = ""
        if len(meta) == 2:
            fields = meta[1].split("\t")
            fields.extend([""] * (5 - len(fields)))
            final_url = fields[0]
            content_type = fields[1]
            size_download = fields[2] or str(body_size)
            try:
                total_ms = str(round(float(fields[3]) * 1000)) if fields[3] else total_ms
            except ValueError:
                pass
            redirects = fields[4]
        status = last_status(headers)
        title = title_from(body)
        blocked = bool(BLOCK_RE.search((title + " " + body[:8192].decode("utf-8", "replace")).encode("utf-8", "replace")))
        try:
            body_bytes = int(float(size_download))
        except ValueError:
            body_bytes = body_size
        html_body = content_type.lower().startswith(("text/html", "application/xhtml"))
        body_valid = bool(status and 200 <= status < 400 and not blocked and (not html_body or bool(title) or body_bytes >= 1000))
        if 200 <= status < 400:
            classification = "remote_clean_candidate" if body_valid else "remote_challenge_or_shell"
        elif status in {404, 410}:
            classification = "remote_likely_dead_candidate"
        elif status in {401, 403, 429}:
            classification = "access_or_rate_limit_warning"
        elif status >= 500:
            classification = "server_or_network_warning"
        else:
            classification = "transport_unresolved"
        error = process.stderr.strip()[:500]
        print(f"{HOST_LABEL} {item['index'] + 1}/{len(URLS)} {status or process.returncode} {url}", file=sys.stderr, flush=True) if (item["index"] + 1) % 25 == 0 else None
        return {
            "original_url": url,
            "prior_classification": item.get("prior_classification", ""),
            "remote_host": HOST_LABEL,
            "remote_hostname": socket.gethostname(),
            "remote_os": sys.platform,
            "http_status": str(status or ""),
            "final_url": final_url,
            "content_type": content_type,
            "body_bytes": str(body_bytes),
            "page_title": title,
            "redirects": redirects,
            "total_ms": total_ms,
            "curl_exit": str(process.returncode),
            "curl_error": error,
            "body_valid": "yes" if body_valid else "no",
            "classification": classification,
            "observed_at": observed_at(),
        }

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
    rows = list(pool.map(run_one, URLS))
print(json.dumps({"hostname": socket.gethostname(), "rows": rows}, ensure_ascii=True))
'''


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in OUTPUT_FIELDS} for row in rows)


def run_remote(host: str, items: list[dict[str, str]], workers: int, timeout: int) -> tuple[list[dict[str, str]], str]:
    payload = {
        "host": host,
        "urls": [
            {"index": index, "url": item["url"], "prior_classification": item.get("final_classification", "")}
            for index, item in enumerate(items)
        ],
        "workers": workers,
        "timeout": timeout,
    }
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "python3", "-"]
    # Embed the JSON as a Python literal in the one-shot remote program. The
    # remote interpreter receives one valid program, not a code/data stream.
    remote_program = REMOTE_CODE.replace(
        "PAYLOAD = json.loads(sys.stdin.read())",
        "PAYLOAD = " + json.dumps(payload, ensure_ascii=True),
    )
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate(remote_program, timeout=max(120, len(items) * timeout // max(1, workers) + 120))
    sys.stderr.write(stderr)
    if process.returncode != 0:
        raise RuntimeError(f"remote probe failed for {host}: exit {process.returncode}: {stderr[-1000:]}")
    # The remote script emits progress only on stderr and one JSON object on stdout.
    result = json.loads(stdout.strip())
    rows = result.get("rows") or []
    return rows, str(result.get("hostname") or "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--host", action="append", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    all_rows = read_rows(args.results)
    items = [
        row
        for row in all_rows
        if row.get("final_classification") not in {"confirmed_get_ok", "confirmed_get_redirect", "likely_dead_not_found"}
    ]
    for host in args.host:
        rows, hostname = run_remote(host, items, max(1, args.workers), max(5, args.timeout))
        path = args.output_dir / (host.replace("@", "_").replace("/", "_") + ".csv")
        write_rows(path, rows)
        print(json.dumps({"host": host, "hostname": hostname, "rows": len(rows), "output": str(path)}))


if __name__ == "__main__":
    main()
