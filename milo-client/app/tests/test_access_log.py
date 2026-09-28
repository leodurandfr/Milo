"""
The access log keeps everything but a volume step that succeeded.

What breaks when this fails: a turn of the knob buries the one log surface a
satellite has (`sat logs`) under access lines, or a refused step, or any other
request — a mute above all — vanishes from it.
"""
import logging

import pytest

from access_log import VolumeStepFilter


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
])
def test_only_a_successful_volume_step_leaves_the_access_log(method, path, status, kept):
    record = _access(method, path, status)
    assert record.getMessage()  # the args are the shape uvicorn formats
    assert VolumeStepFilter().filter(record) is kept
