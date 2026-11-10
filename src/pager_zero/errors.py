"""Error helpers shared by the API and the bench."""

from __future__ import annotations

import traceback


def root_cause(e: BaseException) -> BaseException:
    """Unwrap ExceptionGroups / chained exceptions to the innermost real error."""
    seen = 0
    while seen < 20:
        seen += 1
        if isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        elif e.__cause__ is not None:
            e = e.__cause__
        else:
            return e
    return e


def describe(e: BaseException, limit: int = 300) -> str:
    rc = root_cause(e)
    return f"{type(rc).__name__}: {str(rc)[:limit]}"


def full_trace(e: BaseException) -> str:
    return "".join(traceback.format_exception(e))
