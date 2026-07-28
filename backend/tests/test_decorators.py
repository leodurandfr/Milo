"""`@handle_errors` — the helper the error-handling doctrine mandates.

CLAUDE.md's error table tells every service to reach for
`@handle_errors(default=…, level='error')` on its legitimate-fallback path, so
this decorator decides what a large part of the backend does when something
goes wrong — and it was itself untested. What breaks when these fail is not one
call site but the doctrine: a wrong default turns a failure into a plausible
value, a missing log turns it into silence, and swallowing `CancelledError`
turns every background loop into a task that cannot be stopped.

Consumers: every service using the decorator (SystemdServiceManager,
VolumeService, MpvController, the audio sources, …).
"""
import asyncio
import logging

import pytest

from backend.shared.decorators import handle_errors


class ServiceWithLogger:
    """The `self.logger` shape (VolumeService, MpvController, …)."""

    def __init__(self, logger):
        self.logger = logger

    @handle_errors(default="fallback")
    async def boom(self):
        raise RuntimeError("kaboom")


class ServiceWithPrivateLogger:
    """The `self._logger` shape (BaseAudioSource subclasses)."""

    def __init__(self, logger):
        self._logger = logger

    @handle_errors(default="fallback")
    async def boom(self):
        raise RuntimeError("kaboom")


# --------------------------------------------------------------------------- #
# The fallback contract
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_success_path_is_transparent():
    """A decorated call that works must be indistinguishable from an undecorated one."""

    @handle_errors(default="fallback")
    async def ok(a, b, *, c):
        return (a, b, c)

    assert await ok(1, 2, c=3) == (1, 2, 3)


@pytest.mark.asyncio
async def test_failure_returns_the_declared_default_instead_of_raising():
    """The whole point: an absorbed failure yields the default, not an exception."""

    @handle_errors(default="fallback")
    async def boom():
        raise RuntimeError("kaboom")

    assert await boom() == "fallback"


@pytest.mark.asyncio
async def test_without_a_default_the_error_still_propagates():
    """`@handle_errors()` logs and re-raises — it is not a blanket swallow.

    A caller that omits `default` is asking for the log, not for the failure to
    disappear; turning that into a silent `None` is the anti-pattern the
    doctrine's table calls out.
    """

    @handle_errors()
    async def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        await boom()


def test_sync_functions_get_the_same_contract():
    """Both wrappers exist; a difference between them would be invisible."""

    @handle_errors(default=-1)
    def boom():
        raise ValueError("nope")

    @handle_errors(default=-1)
    def ok():
        return 7

    assert boom() == -1
    assert ok() == 7


@pytest.mark.asyncio
async def test_a_mutable_default_is_not_shared_between_calls():
    """Handing out the same list twice lets one caller corrupt the next one's fallback."""

    @handle_errors(default=[])
    async def boom():
        raise RuntimeError("kaboom")

    first = await boom()
    first.append("polluted")
    assert await boom() == []


# --------------------------------------------------------------------------- #
# The log
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("level,expected", [
    ("error", logging.ERROR),
    ("warning", logging.WARNING),
    ("info", logging.INFO),
    ("debug", logging.DEBUG),
])
async def test_the_absorbed_failure_is_logged_at_the_declared_level(caplog, level, expected):
    """An absorbed failure that logs below its declared level is a silent failure."""

    @handle_errors(default=None, level=level)
    async def boom():
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG):
        await boom()

    records = [r for r in caplog.records if "kaboom" in r.message]
    assert len(records) == 1
    assert records[0].levelno == expected


@pytest.mark.asyncio
async def test_an_exception_with_no_message_still_names_its_type(caplog):
    """`str(e)` is empty for e.g. a bare TimeoutError — the log must not be blank."""

    @handle_errors(default=None)
    async def boom():
        raise asyncio.TimeoutError()

    with caplog.at_level(logging.DEBUG):
        await boom()

    assert any("TimeoutError" in r.message for r in caplog.records)


def test_an_unknown_level_fails_at_decoration_time():
    """A typo'd level must not wait for the first failure to surface."""
    with pytest.raises(ValueError, match="Invalid log level"):
        @handle_errors(default=None, level="critcal")
        def _f():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("service_cls", [ServiceWithLogger, ServiceWithPrivateLogger])
async def test_the_log_goes_to_the_owning_service_logger(service_cls):
    """Both attribute spellings are in use; a miss sends the log to the wrong hierarchy.

    That matters beyond tidiness: the WebSocketLogHandler banner and the
    per-source logger namespaces both key off the logger the record came from.
    """
    logger = logging.getLogger("test.decorators.owner")
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    try:
        assert await service_cls(logger).boom() == "fallback"
    finally:
        logger.removeHandler(handler)

    assert [r.name for r in records] == ["test.decorators.owner"]


@pytest.mark.asyncio
async def test_a_standalone_function_falls_back_to_its_module_logger(caplog):
    """No `self` to read a logger from — the record must still be attributable."""

    @handle_errors(default=None)
    async def boom():
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.DEBUG):
        await boom()

    assert [r.name for r in caplog.records if "kaboom" in r.message] == [__name__]


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cancellation_is_not_absorbed():
    """A decorator that eats cancellation makes every background loop unkillable.

    `BackgroundTaskSet.cancel_all()` and every `_monitor_task` teardown rely on
    `CancelledError` reaching the coroutine's caller. It survives here because
    it derives from BaseException rather than Exception — an implementation
    detail the decorator must not paper over by widening its except clause.
    """
    started = asyncio.Event()

    @handle_errors(default="fallback")
    async def loop_body():
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(loop_body())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
