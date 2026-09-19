"""Retry policy for transient failures (exponential backoff with jitter)."""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.errors import TransientError
from app.core.logging import get_logger

T = TypeVar("T")
log = get_logger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.6
    max_delay: float = 8.0
    jitter: float = 0.25
    retry_on: tuple[type[BaseException], ...] = (TransientError, ConnectionError, TimeoutError)


def retry_call(
    fn: Callable[[], T],
    policy: RetryPolicy | None = None,
    *,
    label: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> T:
    """Run ``fn`` until success or attempts are exhausted. The last exception is re-raised."""
    policy = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except policy.retry_on as exc:
            if attempt >= policy.max_attempts or (should_stop and should_stop()):
                log.warning("%s failed after %d attempt(s): %s", label, attempt, type(exc).__name__)
                raise
            delay = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
            delay *= 1 + random.uniform(-policy.jitter, policy.jitter)
            log.info("%s transient failure (%s); retry %d in %.2fs", label, type(exc).__name__, attempt, delay)
            sleep(delay)
