# backend/core/system/logs/throttling.py
"""The Pi's throttling state, asked of the firmware when the logs page is read.

An undervoltage, a capped frequency, a throttle or the soft temperature limit
is the cause of a dropout no log line explains: the firmware reports it, the
kernel only sometimes (`hwmon: Undervoltage detected!`, and never the
temperature limits). `vcgencmd get_throttled` answers with one word,
`throttled=0x50005`, read here bit by bit — the Raspberry Pi documentation's
layout, low half "now", bits 16–19 "has occurred since boot":

    0 / 16  under-voltage
    1 / 17  ARM frequency capped
    2 / 18  throttled
    3 / 19  soft temperature limit

The `milo` user is in group `video`, which is what `/dev/vcio` asks: no sudo.
A host with no vcgencmd (a dev machine) has no answer, and says so — never a
`0x0` it did not read —, and a Pi whose vcgencmd fails says that instead.
"""
import asyncio
import shutil
from typing import List, Optional, Union

from backend.core.system.diagnostic import probes
from backend.core.system.logs.models import ThrottleCondition, Throttling

# Each condition, the bit that says it holds now; the same 16 bits up says it
# has held since boot.
CONDITIONS = (
    ("under_voltage", 0),
    ("frequency_capped", 1),
    ("throttled", 2),
    ("soft_temperature_limit", 3),
)
_SINCE_BOOT_SHIFT = 16
TIMEOUT_S = 5.0


def parse(output: str) -> Optional[int]:
    """The value of `throttled=0x…`, or None for anything else."""
    key, _, value = output.strip().partition("=")
    if key != "throttled":
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def decode(value: int) -> Throttling:
    """The value, its conditions named, each now and since boot."""
    conditions: List[ThrottleCondition] = [
        ThrottleCondition(
            name=name,
            now=bool(value & (1 << bit)),
            since_boot=bool(value & (1 << (bit + _SINCE_BOOT_SHIFT))),
        )
        for name, bit in CONDITIONS
    ]
    return Throttling(value=value, raw=f"0x{value:x}", conditions=conditions)


async def read() -> Union[Throttling, str]:
    """The state now, or why it could not be read."""
    out = await probes.run(["vcgencmd", "get_throttled"], timeout=TIMEOUT_S)
    if out is None:
        # No answer is three cases — absent, refused (VCHI, `/dev/vcio`), no
        # reply in time —, and only the first says the host is not a Pi.
        if await asyncio.to_thread(shutil.which, "vcgencmd") is None:
            return "vcgencmd is not installed (not a Raspberry Pi)"
        return f"vcgencmd get_throttled failed or did not answer within {TIMEOUT_S:g} s"
    value = parse(out)
    if value is None:
        return f"vcgencmd get_throttled answered {out.strip()[:80]!r}"
    return decode(value)
