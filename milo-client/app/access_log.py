"""Keep the per-step volume requests out of the access log.

`sat logs` is the only log surface a satellite has, and a turn of the knob
filled it: on Bureau, 185 of 400 journal lines were volume steps, each followed
by its access line. A refused step (4xx/5xx) still logs, and so does a mute:
it is rare, and a silent room is the first thing that log is read for.
"""
import logging

QUIET_PATH = "/equalizer/volume"


class VolumeStepFilter(logging.Filter):
    """Drop uvicorn's access line for a volume step that succeeded.

    uvicorn.access passes (client_addr, method, path, http_version, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, path, _, status = args
        return not (method == "PUT" and path == QUIET_PATH and status < 400)


def quiet_volume_steps() -> None:
    logging.getLogger("uvicorn.access").addFilter(VolumeStepFilter())
