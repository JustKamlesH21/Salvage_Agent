#!/usr/bin/env python3
"""Webhook capture receiver. Day 1 only — this is not the ingest endpoint.

Deliberately separate from `api/ingest/`, which lands on day 2. This process
does one thing: take whatever Razorpay delivers and write it to disk without
touching it.

    RAW BYTES ARE READ BEFORE ANYTHING ELSE HAPPENS.

`await request.body()` is the first statement in the handler. Nothing parses,
decodes, re-serialises or validates before the bytes are on disk. Phase 2's
HMAC verification runs against those bytes, and a body that has been through
json.loads/json.dumps is a different byte string with a different signature —
key order, whitespace and unicode escaping all change. A re-serialised fixture
would let a broken HMAC implementation pass.

This receiver does NOT verify the signature. It stores the
`X-Razorpay-Signature` header alongside the body so day 2 can. Verifying here
would mean day 1 asserting the thing day 2 is supposed to prove.

Output, per delivery, into tests/fixtures/_unredacted/ (gitignored):

    <nnn>-<event>.raw.json        the body, byte for byte
    <nnn>-<event>.headers.json    every header, signature and event id included
    <nnn>-<event>.meta.json       when it arrived, byte count, sha256

Run:  .venv/bin/python scripts/capture_webhooks.py --port 8001
Then point a tunnel at that port and register the tunnel URL in the Dashboard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "_unredacted"

app = FastAPI(title="Salvage webhook capture", version="0.1.0")


def next_sequence() -> int:
    existing = sorted(OUT.glob("*.raw.json"))
    return len(existing) + 1


@app.post("/webhooks/razorpay")
async def capture(request: Request) -> JSONResponse:
    # First statement. Nothing above this line may touch the body.
    raw: bytes = await request.body()

    received_at = dt.datetime.now(dt.UTC)
    headers = dict(request.headers)
    digest = hashlib.sha256(raw).hexdigest()

    # Parsed only to name the file. The parse result is never written back
    # into the raw fixture.
    try:
        event = str(json.loads(raw).get("event", "unknown")).replace(".", "_")
    except (json.JSONDecodeError, AttributeError):
        event = "unparseable"

    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{next_sequence():03d}-{event}"

    (OUT / f"{stem}.raw.json").write_bytes(raw)
    (OUT / f"{stem}.headers.json").write_text(
        json.dumps(headers, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / f"{stem}.meta.json").write_text(
        json.dumps(
            {
                "received_at": received_at.isoformat(),
                "byte_count": len(raw),
                "sha256": digest,
                "event": event.replace("_", "."),
                "signature_header": headers.get("x-razorpay-signature"),
                "razorpay_event_id": headers.get("x-razorpay-event-id"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"captured {stem}  {len(raw):>6} bytes  sha256={digest[:12]}…")
    # Razorpay retries on non-2xx. Always 200 so a capture is never duplicated
    # by a retry we caused ourselves.
    return JSONResponse({"status": "captured", "file": stem}, status_code=200)


@app.get("/health")
async def health() -> dict[str, Any]:
    captured = sorted(p.name for p in OUT.glob("*.raw.json")) if OUT.exists() else []
    return {"status": "ok", "captured": len(captured), "files": captured}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Razorpay webhooks to fixtures.")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    print(f"capture: listening on 127.0.0.1:{args.port}/webhooks/razorpay")
    print(f"capture: writing to {OUT.relative_to(ROOT)}/ (gitignored)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
