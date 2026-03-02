# backend/shared/decorators.py
"""
Reusable decorators for error handling.
"""
import copy
import functools
import inspect
import logging
from typing import Any, Callable, TypeVar

F = TypeVar('F', bound=Callable[..., Any])

# Sentinel for "no default provided" - distinct from None
_MISSING = object()

# Types that are safe to return directly (immutable)
_IMMUTABLE_TYPES = (type(None), bool, int, float, str, tuple, frozenset, type)


def _get_logger(func: Callable, first_arg: Any) -> logging.Logger:
    """
    Resolve the logger for a given call.

    Priority:
    1. first_arg.logger  (self.logger — VolumeService, MpvController, etc.)
    2. first_arg._logger (self._logger — BaseAudioSource subclasses)
    3. logging.getLogger(func.__module__) fallback for standalone functions
    """
    for attr in ('logger', '_logger'):
        logger = getattr(first_arg, attr, None)
        if isinstance(logger, logging.Logger):
            return logger
    return logging.getLogger(func.__module__)


def handle_errors(
    *,
    default: Any = _MISSING,
    level: str = 'error',
):
    """
    Catch Exception, log it, and return a default value.

    Works with both sync and async methods. Auto-detects the logger from
    self.logger, self._logger, or falls back to the module logger.

    Mutable defaults (list, dict) are shallow-copied on each error to prevent
    shared-state corruption across calls.

    Args:
        default: Value to return on error. If omitted, the exception is re-raised.
        level: Log level: 'error' (default), 'warning', or 'debug'.

    Usage:
        @handle_errors(default=False)
        async def start(self) -> bool: ...

        @handle_errors(default=[], level='warning')
        async def get_items(self) -> list: ...
    """
    if level not in ('error', 'warning', 'debug'):
        raise ValueError(f"Invalid log level '{level}'. Must be: error, warning, debug")

    has_default = default is not _MISSING
    needs_copy = has_default and not isinstance(default, _IMMUTABLE_TYPES)

    def decorator(fn: F) -> F:
        method_name = fn.__name__

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    logger = _get_logger(fn, args[0] if args else None)
                    getattr(logger, level)(f"Error in {method_name}: {e}")
                    if has_default:
                        return copy.copy(default) if needs_copy else default
                    raise
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    logger = _get_logger(fn, args[0] if args else None)
                    getattr(logger, level)(f"Error in {method_name}: {e}")
                    if has_default:
                        return copy.copy(default) if needs_copy else default
                    raise
            return sync_wrapper

    return decorator
