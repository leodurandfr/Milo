# backend/core/system/localtime.py
"""The zone in force, read once for everyone who reports it.

Two places state it — `GET /api/system/timezone` (and the daylight location the
kiosk's theme reads) and the diagnostic report's identity line — and they read
the same link, so they read it through this one function: a second copy of the
parse is how the report kept printing timedated's raw relative path after the
API had learned to resolve it.
"""
import os
from typing import Optional

LOCALTIME_LINK = "/etc/localtime"
ZONEINFO_PREFIX = "/usr/share/zoneinfo/"


def current_timezone() -> Optional[str]:
    """The zone in force, read from what the C library actually follows.

    `/etc/localtime` is the file every timestamp on this box resolves through,
    so it cannot disagree with reality the way a second copy in /etc/timezone
    can. Returns None when it is not a symlink into the zoneinfo tree — an
    answer, not a guess.

    timedated writes the link relative (`../usr/share/zoneinfo/…`), so it is
    joined to where the link sits — never `realpath`'d, which would follow an
    alias (`UTC` → `Etc/UTC`) and report a zone nobody picked.
    """
    try:
        target = os.readlink(LOCALTIME_LINK)
    except OSError:
        return None
    if not os.path.isabs(target):
        target = os.path.normpath(os.path.join(os.path.dirname(LOCALTIME_LINK), target))
    if not target.startswith(ZONEINFO_PREFIX):
        return None
    return target[len(ZONEINFO_PREFIX):]
