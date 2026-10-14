"""Shared PostgreSQL transaction helpers for Nuvella.

Only retry complete, replayable database transactions here. Retrying an individual
query inside a failed transaction is not sufficient because PostgreSQL marks the
transaction aborted after a deadlock/serialization failure.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
log = logging.getLogger("nuvella.database")

_RETRYABLE_SQLSTATES = {"40P01", "40001"}  # deadlock_detected, serialization_failure


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)


async def run_transaction(
    pool,
    operation: Callable[[object], Awaitable[T]],
    *,
    label: str = "transaction",
    attempts: int = 4,
    base_delay: float = 0.05,
) -> T:
    """Run a database-only operation in a transaction, retrying transient conflicts.

    ``operation`` is re-executed from the beginning after a deadlock or
    serialization error, so it must not perform Discord/API/file side effects.
    The operation should use the supplied connection and return its result.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(1, attempts + 1):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    return await operation(conn)
        except Exception as exc:
            if _sqlstate(exc) not in _RETRYABLE_SQLSTATES or attempt >= attempts:
                if _sqlstate(exc) in _RETRYABLE_SQLSTATES:
                    log.exception(
                        "Database transaction exhausted retries | label=%s | attempts=%s | sqlstate=%s",
                        label, attempts, _sqlstate(exc),
                    )
                raise

            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
            log.warning(
                "Retrying database transaction after lock conflict | label=%s | attempt=%s/%s | sqlstate=%s | delay=%.3fs",
                label, attempt, attempts, _sqlstate(exc), delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("Unreachable transaction retry state")
