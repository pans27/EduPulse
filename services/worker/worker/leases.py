"""Lease settings, validated at startup so every accepted value leaves time to save a result."""

# Time kept back from the lease for writing the outcome after the network work is done.
WRITE_MARGIN_SECONDS = 10
# Shortest useful time for the network work itself (one check, or one email send).
MIN_WORK_SECONDS = 5


def work_deadline(lease_seconds: int, *, name: str) -> int:
    """Seconds the network work may take under this lease; rejects leases too short to be safe."""
    shortest = WRITE_MARGIN_SECONDS + MIN_WORK_SECONDS
    if lease_seconds < shortest:
        raise ValueError(
            f"{name}={lease_seconds} is too short: it must be at least {shortest} s "
            f"({MIN_WORK_SECONDS} s of work + {WRITE_MARGIN_SECONDS} s to save the result)"
        )
    return lease_seconds - WRITE_MARGIN_SECONDS
