"""
The access log keeps everything but a volume step or a levels poll that succeeded.

What breaks when this fails: a turn of the knob, or the 10 Hz levels poll of an
open EQ modal, buries the one log surface a satellite has (`sat logs`) under
access lines, or a refused request, or any other request — a mute above all —
vanishes from it. Method and path count together: only the pair is quiet.
"""
import logging

import pytest

from access_log import QuietRequestFilter


def _access(method, path, status):
    """The record uvicorn.access emits: %s - "%s %s HTTP/%s" %d."""
    return logging.LogRecord("uvicorn.access", logging.INFO, "h11_impl.py", 0,
                             '%s - "%s %s HTTP/%s" %d',
                             ("192.168.1.10:51234", method, path, "1.1", status), None)


@pytest.mark.parametrize("method, path, status, kept", [
    ("PUT", "/equalizer/volume", 200, False),
    ("PUT", "/equalizer/mute", 200, True),
    ("PUT", "/equalizer/volume", 400, True),
    ("GET", "/equalizer/volume", 200, True),
    ("PUT", "/equalizer/filters", 200, True),
    ("POST", "/app/update", 200, True),
    ("GET", "/equalizer/levels", 200, False),
    ("GET", "/equalizer/levels", 500, True),
    ("PUT", "/equalizer/levels", 200, True),
])
def test_only_a_successful_volume_step_or_levels_poll_leaves_the_access_log(method, path, status, kept):
    record = _access(method, path, status)
    assert record.getMessage()  # the args are the shape uvicorn formats
    assert QuietRequestFilter().filter(record) is kept
