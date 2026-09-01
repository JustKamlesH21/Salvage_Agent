"""The single choke point for every Razorpay call.

`client.py` owns the SDK import (CLAUDE.md invariant 1). `execute_action()`
owns the bounded A1-A10 action catalog (SPEC §4.2) and lands on day 6.

The two are not the same boundary. Setup work — creating plans, customers and
subscriptions — goes through the SDK boundary via `get_razorpay_client()` but
is not a catalog action and must never be added to the catalog: a bounded list
that grows to hold setup verbs has stopped being bounded.
"""

from api.executor.client import get_razorpay_client

__all__ = ["get_razorpay_client"]
