from __future__ import annotations


def ok(status: int) -> bool:
    """True for any 2xx response (the LCU uses 200/201/204 interchangeably)."""
    return 200 <= status < 300
