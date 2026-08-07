#!/usr/bin/env python3
"""Resolve benchmark result URLs without retaining page bodies."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


USER_AGENT = "web-search-benchmark-url-resolver/1.0"
MAX_PREVIEW_BYTES = 65536
TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def clean_title(body: bytes) -> str:
    match = TITLE_RE.search(body)
    if not match:
        return ""
    value = re.sub(rb"\s+", b" ", match.group(1)).strip()
    return value.decode("utf-8", errors="replace")[:500]


def open_request(url: str, method: str, timeout: float):
    request = Request(
        url,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.2",
            "Range": f"bytes=0-{MAX_PREVIEW_BYTES - 1}",
        },
    )
    return urlopen(request, timeout=timeout)


def resolve(url: str, get_only: bool = False, timeout: float = 15.0) -> dict[str, object]:
    parsed = urlparse(url)
    request_url = quote(url, safe=":/%?&=+#[]@!$'()*;,~")
    base = {
        "original_url": url,
        "final_url": "",
        "http_status": "",
        "content_type": "",
        "redirected": "",
        "broken": "",
        "error": "",
        "page_title": "",
    }
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        base.update({"broken": "1", "error": "invalid_url"})
        return base


    response = None
    if not get_only:
        try:
            response = open_request(request_url, "HEAD", timeout)
            status = int(response.status)
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            response.close()
            if status in {400, 403, 405, 406, 429, 500, 501, 502, 503}:
                raise RuntimeError(f"head_status_{status}")
            base.update(
                {
                    "final_url": final_url,
                    "http_status": status,
                    "content_type": content_type,
                    "redirected": "1" if final_url != url else "0",
                    "broken": "1" if status >= 400 else "0",
                }
            )
            return base
        except Exception:
            if response is not None:
                response.close()

    try:
        response = open_request(request_url, "GET", timeout)
        preview = response.read(MAX_PREVIEW_BYTES)
        status = int(response.status)
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
        response.close()
        base.update(
            {
                "final_url": final_url,
                "http_status": status,
                "content_type": content_type,
                "redirected": "1" if final_url != url else "0",
                "broken": "1" if status >= 400 else "0",
                "page_title": clean_title(preview) if "html" in content_type else "",
            }
        )
        return base
    except HTTPError as error:
        if response is not None:
            response.close()
        base.update(
            {
                "final_url": error.geturl(),
                "http_status": int(error.code),
                "redirected": "1" if error.geturl() != url else "0",
                "broken": "1",
                "error": f"http_{error.code}",
            }
        )
        return base
    except (URLError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        if response is not None:
            response.close()
        base.update({"broken": "1", "error": str(error)[:500]})
        return base
    except Exception as error:
        if response is not None:
            response.close()
        base.update({"broken": "1", "error": str(error)[:500]})
        return base


def resolve_curl_get(
    url: str,
    timeout: float,
    no_range: bool = False,
    user_agent: str = USER_AGENT,
    ip_family: str = "",
    insecure: bool = False,
    http1: bool = False,
    retries: int = 0,
    retry_delay: float = 0.0,
) -> dict[str, object]:
    """Perform a bounded GET and discard the body after a small range."""
    base = {
        "original_url": url,
        "final_url": "",
        "http_status": "",
        "content_type": "",
        "redirected": "",
        "broken": "",
        "error": "",
        "page_title": "",
    }
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        base.update({"broken": "1", "error": "invalid_url"})
        return base

    format_string = "%{http_code}\t%{url_effective}\t%{content_type}\t%{time_total}\t%{errormsg}"
    command = [
        "/usr/bin/curl",
        "-L",
        "-sS",
        "--compressed",
        "--connect-timeout",
        str(min(timeout, 8.0)),
        "--max-time",
        str(timeout),
        "--max-redirs",
        "10",
        "-A",
        user_agent,
        "-o",
        "/dev/null",
        "-w",
        format_string,
        url,
    ]
    if ip_family:
        command[1:1] = [ip_family]
    if insecure:
        command[1:1] = ["--insecure"]
    if http1:
        command[1:1] = ["--http1.1"]
    if retries:
        command[1:1] = [
            "--retry",
            str(max(0, retries)),
            "--retry-all-errors",
            "--retry-delay",
            str(int(max(0.0, retry_delay))),
            "--retry-max-time",
            str(int(timeout * (max(0, retries) + 1) + max(0.0, retry_delay) * max(0, retries) + 2.0)),
        ]
    if not no_range:
        command[4:4] = ["--range", "0-65535"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout * (max(0, retries) + 1) + max(0.0, retry_delay) * max(0, retries) + 5.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        base.update({"broken": "1", "error": "curl_process_timeout"})
        return base

    fields = completed.stdout.strip().split("\t", 4)
    status_text, final_url, content_type, elapsed, curl_error = (fields + [""] * 5)[:5]
    try:
        status = int(status_text)
    except ValueError:
        status = 0
    error = curl_error.strip()
    if completed.returncode != 0:
        error = "; ".join(value for value in [f"curl_exit_{completed.returncode}", error, completed.stderr.strip()] if value)
    base.update(
        {
            "final_url": final_url,
            "http_status": status if status else "",
            "content_type": content_type,
            "redirected": "1" if final_url and final_url != url else "0",
            "broken": "1" if status >= 400 or completed.returncode != 0 else "0",
            "error": error[:500],
        }
    )
    return base

def read_urls(paths: list[Path]) -> list[str]:
    values: set[str] = set()
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                value = (row.get("canonical_url") or row.get("url") or "").strip()
                if value:
                    values.add(value)
    return sorted(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--get-only", action="store_true", help="Use GET for every URL instead of accepting a HEAD response.")
    parser.add_argument("--no-range", action="store_true", help="Do not add the bounded Range header to GET-only curl checks.")
    parser.add_argument("--user-agent", default=USER_AGENT)
    parser.add_argument("--ip-family", choices=["", "--ipv4", "--ipv6"], default="")
    parser.add_argument("--insecure", action="store_true", help="Allow invalid TLS certificates for reachability diagnosis.")
    parser.add_argument("--http1", action="store_true", help="Force HTTP/1.1 for protocol compatibility diagnosis.")
    parser.add_argument("--curl-retries", type=int, default=0, help="Retry failed curl transfers this many times.")
    parser.add_argument("--curl-retry-delay", type=float, default=0.0, help="Seconds between curl retries.")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    urls = read_urls(args.inputs)
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        resolver = resolve_curl_get if args.get_only else resolve
        futures = {
            pool.submit(
                resolver,
                url,
                args.timeout,
                args.no_range,
                args.user_agent,
                args.ip_family,
                args.insecure,
                args.http1,
                args.curl_retries,
                args.curl_retry_delay,
            ) if args.get_only else pool.submit(resolver, url): url
            for url in urls
        }
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if index % 100 == 0 or index == len(futures):
                print(f"resolved {index}/{len(futures)} URLs", flush=True)

    rows.sort(key=lambda row: str(row["original_url"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "original_url",
        "final_url",
        "http_status",
        "content_type",
        "redirected",
        "broken",
        "error",
        "page_title",
    ]
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} URL resolutions to {args.out}")


if __name__ == "__main__":
    main()
