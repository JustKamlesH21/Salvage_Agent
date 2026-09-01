# Architecture

> **Day 0 stub.** The diagram below is the target from `SALVAGE_SPEC.md` §5.
> The *why* for each boundary is written as each boundary is built, and this
> file is complete on day 10. What is here now is the part that is already
> true: the two boundaries the scaffolding enforces.

```
Razorpay Test Mode ──webhooks──▶ [1] INGEST        day 2
                                     ▼
                                 [2] NORMALISE     day 3
                                     ▼
                                 [3] DIAGNOSE      day 3-4   ← the only LLM
                                     ▼
                                 [4] POLICY        day 4     ← deterministic
                                     ▼
                                 [5] GATES G0-G10  day 5     ← non-bypassable
                                     ▼
                                 [6] EXECUTOR      day 6     ← the only door
                                     ▼
                        [7] SCHEDULER   [8] OUTCOME ENGINE   day 6-7
                                     ▼
                        [9] EVAL  →  [10] AUDIT API → web    day 8-9
```

## The two boundaries that already exist

**The Razorpay SDK is imported in exactly one file.**
`api/executor/client.py`, and nowhere else in `api/`.
`scripts/check_boundaries.py` AST-walks the package and fails the build
otherwise. This is the architectural answer to *"how do we know your agent can't
go rogue"*: there is one door, and the build breaks if someone cuts a second
one. `scripts/seed_razorpay.py` is deliberately outside the scanned roots — it
is setup, not a runtime path, and it must never be imported by anything under
`api/`.

**`events` and `audit` are append-only, in two layers.**
Their repositories expose `insert` and reads; no update, delete or replace
method exists on them, and `check_boundaries.py` fails the build if one is
added or if a mutating driver call targets either collection. On top of that,
every document carries `prev_hash` and `hash`, so a modification made outside
the application is still detectable — `scripts/verify_chain.py` walks both
chains and distinguishes a MISMATCH (a document was edited) from an ORPHAN (one
was deleted) from a FORK (two writers appended to the same head).

The chain is tamper-*evident*, not tamper-*proof*. It detects modification; it
does not prevent it. A production system would need append-only storage or a
WORM bucket. That distinction is in `LIMITATIONS.md` and should be said out loud
rather than glossed over.
