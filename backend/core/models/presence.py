# backend/core/models/presence.py
"""The wire shapes of a proof of presence (`core/system/presence.py`).

Here rather than beside the service because the WS event that carries a
challenge (`SystemPresenceChanged`) and the service that opens it both need
them, and the service broadcasts that event.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Gesture(str, Enum):
    """What the unit waits for: the first of the three this unit can offer."""
    ROTARY = "rotary"
    POWER_BUTTON = "power_button"
    POWER_CYCLE = "power_cycle"


class PresenceAction(str, Enum):
    """What a challenge was opened for: one member per guarded route.

    `tests/architecture/test_presence_guard.py` drives every member through
    the route that requires it, so a member with no route, or a route
    guarded under a member it does not test, fails there.
    """
    SSH_OPEN = "ssh_open"
    PASSWORD = "password"
    PROGRAM_UPSTREAM = "program_upstream"
    RESET_SETUP = "reset_setup"
    # Asked only of a client of the recovery access point, once the unit is set
    # up (`api/middleware.py::RecoveryAccessPointGuard`).
    WIFI_SAVE = "wifi_save"
    # The same two doors on a satellite, relayed by this unit
    # (`api/multiroom.py`): a proof given for one never opens the other.
    SATELLITE_SSH_OPEN = "satellite_ssh_open"
    SATELLITE_PASSWORD = "satellite_password"


class PresenceChallenge(BaseModel):
    """A gesture the unit waits for before a request that reaches root.

    `status` is `pending` until the gesture is made on the unit, then
    `confirmed` until the request that opened the challenge is sent again with
    its token. `remaining_s` is what is left of whichever of the two windows
    is running. `cooldown_s` is how long the unit opens no other challenge
    once this one ends unspent, so a dialog that says "ask again" can say
    when. `id` names the challenge and is public; the token that spends it
    travels only in the 428 that opened it.
    """
    id: str
    action: PresenceAction
    gesture: Gesture
    status: Literal["pending", "confirmed"]
    remaining_s: int
    cooldown_s: int
