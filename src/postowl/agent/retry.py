from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_escalation(
    fn: Callable[..., T],
    args: tuple = (),
    kwargs: dict | None = None,
    max_retries: int = 3,
    on_retry: Callable[[int, Exception], dict] | None = None,
) -> T:
    """Call *fn* with escalating retry strategy.

    Level 1: direct retry.
    Level 2: *on_retry* callback adjusts kwargs (e.g. truncate input).
    Level 3: *on_retry* makes final adjustment, then raise if still fails.
    """
    kwargs = dict(kwargs) if kwargs else {}
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, e)
            if attempt < max_retries and on_retry:
                adjustments = on_retry(attempt, e)
                kwargs.update(adjustments)

    raise last_error  # type: ignore[misc]
