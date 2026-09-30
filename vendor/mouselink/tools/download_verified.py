"""Download a known official artifact in byte ranges and verify its SHA-256."""
import argparse
import concurrent.futures
import hashlib
from pathlib import Path
import threading
import time

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    response = session.get(args.url, headers={"Range": "bytes=0-0"}, timeout=30)
    response.raise_for_status()
    if response.status_code != 206:
        raise RuntimeError("Server does not support ranges")
    total = int(response.headers["Content-Range"].split("/")[-1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    chunk = 4 * 1024 * 1024
    jobs = [(start, min(total - 1, start + chunk - 1)) for start in range(0, total, chunk)]
    progress = 0
    began = time.monotonic()
    lock = threading.Lock()

    def fetch(job):
        start, end = job
        error = None
        for attempt in range(4):
            try:
                with requests.Session() as worker:
                    worker.trust_env = False
                    result = worker.get(args.url, headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 30))
                    result.raise_for_status()
                    if result.status_code != 206 or result.headers.get("Content-Range") != f"bytes {start}-{end}/{total}":
                        raise RuntimeError("Unexpected range response")
                    if len(result.content) != end - start + 1:
                        raise RuntimeError("Incomplete response")
                    with lock:
                        with partial.open("r+b") as dest:
                            dest.seek(start)
                            dest.write(result.content)
                    return len(result.content)
            except (requests.RequestException, RuntimeError) as exc:
                error = exc
                time.sleep(attempt + 1)
        raise error

    with partial.open("wb") as dest:
        dest.truncate(total)
    print(f"Downloading {total / 1024**2:.1f} MiB from {args.url.split('/')[2]}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, job) for job in jobs]
        last_print = 0
        for completed in concurrent.futures.as_completed(futures):
            progress += completed.result()
            now = time.monotonic()
            if now - last_print > 10 or progress == total:
                print(f"{progress / total:.0%}  {progress / 1024**2:.1f} MiB  {progress / 1024**2 / (now-began):.1f} MiB/s", flush=True)
                last_print = now
    with partial.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != args.sha256.lower():
        raise RuntimeError(f"SHA256 mismatch: {actual}")
    partial.replace(args.output)
    print(f"SHA256 OK: {actual}", flush=True)


if __name__ == "__main__":
    main()
