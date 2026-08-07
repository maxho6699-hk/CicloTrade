# -*- coding: utf-8 -*-
"""Push idempotent strategy events or equity snapshots to CicloTrade."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PATHS = {"event": "/api/v1/quant/events", "snapshot": "/api/v1/quant/snapshots"}


def push(base_url: str, kind: str, payload: dict, token: str, retries: int = 3) -> dict:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("production strategy uploads require HTTPS")
    if kind not in PATHS or not isinstance(payload, dict):
        raise ValueError("kind and payload are invalid")
    if len(token) < 32:
        raise ValueError("TRADEAI_STRATEGY_INGEST_TOKEN must contain at least 32 characters")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    request = Request(
        base_url.rstrip("/") + PATHS[kind],
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(max(1, min(int(retries), 5))):
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise RuntimeError(f"CicloTrade rejected the upload with HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt + 1 >= retries:
                raise RuntimeError("CicloTrade upload could not reach the server") from exc
        time.sleep(2**attempt)
    raise RuntimeError("CicloTrade upload failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=PATHS)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default=os.getenv("CICLOTRADE_URL", "https://ciclotrade.com"))
    args = parser.parse_args()
    token = os.getenv("TRADEAI_STRATEGY_INGEST_TOKEN", "")
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    result = push(args.base_url, args.kind, payload, token)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
