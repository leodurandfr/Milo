"""Keep the satellite's two frequent requests out of the access log.

`sat logs` is the only log surface a satellite has, and two requests filled it:
a turn of the knob (on Bureau, 185 of 400 journal lines were volume steps, each
followed by its access line), and the levels poll the server's LevelsMonitor
sends every 0.1 s while the EQ modal targets the satellite and for 15 s after
(143 lines in 15 s on Canapé). A refused request (4xx/5xx) still logs, and so
does a mute: it is rare, and a silent room is the first thing that log is read for.
"""
import logging

QUIET_REQUESTS = frozenset({
    ("PUT", "/equalizer/volume"),
    ("GET", "/equalizer/levels"),
})


class QuietRequestFilter(logging.Filter):
    """Drop uvicorn's access line for a volume step or a levels poll that succeeded.

    uvicorn.access passes (client_addr, method, path, http_version, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, path, _, status = args
        return not ((method, path) in QUIET_REQUESTS and status < 400)


def quiet_frequent_requests() -> None:
    logging.getLogger("uvicorn.access").addFilter(QuietRequestFilter())
