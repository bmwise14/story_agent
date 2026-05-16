"""
Exponential backoff with full jitter.

Full jitter: sleep = random.uniform(0, cap)
NOT bounded jitter (random.uniform(cap/2, cap)) because full jitter minimizes
peak load on the upstream during a recovery window — all retriers don't wake
up at the same time.

Interview line: "Full jitter — not bounded jitter — because it's been shown to
minimize peak load on the upstream during a recovery window. Every client picks
a random point between 0 and the cap, so even if thousands of requests all hit
a 429 simultaneously, the retry wave is spread out instead of synchronized."
"""

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_RETRYABLE = (Exception,)  # narrowed per call site via retryable_exceptions param


def with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    retryable_exceptions: tuple = (),
) -> T:
    """
    Call fn() with exponential backoff + full jitter on retryable exceptions.

    Args:
        fn:                    Zero-argument callable to retry.
        max_attempts:          Total attempts before raising.
        base_delay:            Initial delay cap in seconds (doubles each attempt).
        max_delay:             Hard ceiling on the delay cap.
        retryable_exceptions:  Exception types that trigger a retry.
                               If empty, retries on any Exception (use carefully).

    Returns:
        Whatever fn() returns on success.

    Raises:
        The last exception from fn() after max_attempts are exhausted.
    """
    exceptions = retryable_exceptions or (Exception,)
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except exceptions as e:
            last_exc = e
            if attempt == max_attempts - 1:
                raise
            cap = min(max_delay, base_delay * (2 ** attempt))
            sleep_for = random.uniform(0, cap)
            print(f"  [retry] attempt {attempt + 1}/{max_attempts} failed: {e!r} — sleeping {sleep_for:.2f}s")
            time.sleep(sleep_for)

    raise last_exc  # unreachable but satisfies type checker
