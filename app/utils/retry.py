from __future__ import annotations

import asyncio
import functools
import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F")


def async_retry(
    *,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator that retries an async function on specified exceptions.

    Args:
        max_attempts: Maximum number of total attempts (including first).
        delay: Initial delay in seconds between retries.
        backoff: Multiplier applied to delay after each retry.
        exceptions: Tuple of exception types that trigger a retry.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)  # type: ignore[arg-type]
        async def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            current_delay = delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        "retry attempt %d/%d for %s: %s",
                        attempt,
                        max_attempts,
                        func.__qualname__,
                        exc,
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
