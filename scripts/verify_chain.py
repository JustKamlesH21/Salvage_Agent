#!/usr/bin/env python3
"""Walk the `events` and `audit` hash chains and report any break.

PRD §8.4 layer 2. This is the command to run on camera: it is what lets you say
"this audit trail is tamper-evident, and here is the proof" rather than asking
the panel to take it on faith.

Three failure modes are distinguished, because they mean different things:

  MISMATCH  a document's stored hash does not recompute from its own content
            → that document was edited after it was written.
  ORPHAN    a document is not reachable from genesis by prev_hash linkage
            → a document was deleted, or one was inserted out of band.
  FORK      two documents claim the same prev_hash
            → two writers appended to the same head. The unique index on
              prev_hash is supposed to make this impossible; seeing it means
              the index is missing.

An empty collection is a valid chain and is reported as such — vacuous, not
passing under false colours.

Exit 0 clean, 1 on any break or on a connection failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db.chain import ChainBreakError, order_chain, verify_sequence  # noqa: E402
from api.db.collections import APPEND_ONLY  # noqa: E402
from pymongo import ASCENDING, MongoClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402


def load_env() -> tuple[str, str]:
    """Read MONGO_URI / MONGO_DB from the environment, falling back to .env."""
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    uri = os.environ.get("MONGO_URI") or values.get("MONGO_URI", "")
    db = os.environ.get("MONGO_DB") or values.get("MONGO_DB", "salvage")
    return uri, db


def verify_collection(client: MongoClient[Any], db_name: str, name: str) -> list[str]:
    problems: list[str] = []
    collection = client[db_name][name]
    documents = list(collection.find({}).sort([("_id", ASCENDING)]))

    if not documents:
        print(f"chain[{name}]: 0 documents — chain is empty, nothing to verify")
        return problems

    try:
        ordered = order_chain(documents)
    except ChainBreakError as exc:
        return [f"{name}: FORK — {exc}"]

    if len(ordered) != len(documents):
        reachable = {id(d) for d in ordered}
        orphans = [d for d in documents if id(d) not in reachable]
        preview = ", ".join(str(d.get("_id")) for d in orphans[:5])
        problems.append(
            f"{name}: ORPHAN — {len(orphans)} of {len(documents)} document(s) are not reachable "
            f"from genesis. A document was deleted, or one was written outside the repository. "
            f"First unreachable: {preview}"
        )

    try:
        checked = verify_sequence(ordered)
    except ChainBreakError as exc:
        problems.append(f"{name}: MISMATCH — {exc}")
        return problems

    print(f"chain[{name}]: {checked} document(s) verified from genesis to head")
    return problems


def main() -> int:
    uri, db_name = load_env()
    if not uri:
        print(
            "chain: FAIL — MONGO_URI is not set. Copy .env.example to .env and fill in your "
            "Atlas connection string, or export MONGO_URI."
        )
        return 1

    try:
        client: MongoClient[Any] = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        client.admin.command("ping")
    except PyMongoError as exc:
        print(
            f"chain: FAIL — cannot reach MongoDB at the configured URI. The chain cannot be "
            f"verified without it, so this is a failure and not a skip. Driver said: {exc}"
        )
        return 1

    problems: list[str] = []
    for name in sorted(APPEND_ONLY):
        problems += verify_collection(client, db_name, name)
    client.close()

    if problems:
        print(f"chain: FAIL — {len(problems)} break(s)")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("chain: OK — every append-only chain intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
