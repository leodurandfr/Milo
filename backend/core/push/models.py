# backend/core/push/models.py
"""Value types for the APNs token registry.

Both enums exist because the failure they prevent is silent. APNs accepts a
payload addressed to the wrong kind of token and a token addressed to the
wrong host without telling the provider anything useful, so a plain string in
either position is a push that is delivered nowhere and logged as a success.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class PushTokenKind(str, Enum):
    """What a token is allowed to receive.

    The three are not interchangeable and they are not derivable from one
    another: iOS issues a separate token for each role, and the same device
    holds all three at once.

    * ``WIDGET`` — the WidgetKit refresh push (``apns-push-type: widgets``).
    * ``PUSH_TO_START`` — receives the ``start`` event, and nothing else. It is
      long-lived: it exists before any session does.
    * ``SESSION`` — receives ``update`` and ``end`` for exactly one session, and
      dies with it.
    """
    WIDGET = "widget"
    PUSH_TO_START = "push_to_start"
    SESSION = "session"


class ApnsEnvironment(str, Enum):
    """Which APNs host issued the token, and therefore which one accepts it.

    A Debug build's token is only valid against ``api.sandbox.push.apple.com``.
    Sent to the production host it answers ``BadDeviceToken`` — a 400 that looks
    exactly like a malformed token, with nothing on the device to see. This is
    recorded per token rather than configured per unit because one appliance can
    legitimately hold both: a TestFlight build and a Debug build on the same
    phone produce tokens on different hosts.

    **These names are the APNs HOSTS, not Apple's entitlement values.** The
    `aps-environment` entitlement an iOS app reads spells the same two things
    ``development`` and ``production``, so a client that forwards its
    entitlement verbatim sends ``development`` and takes a 422. Measured on
    Milo-iOS, 2026-09-19: because that client decodes only ``status``, the 422
    was indistinguishable from a network failure and the registration simply
    never happened.

    The names stay as they are, and the translation stays on the client, which
    is the side that speaks the entitlement dialect. Accepting both spellings
    here would be a two-key chain for one concept — the shape CLAUDE.md's "no
    legacy / migration code" rules out — and it would leave the backend unable
    to say which vocabulary a stored token was registered under.
    """
    SANDBOX = "sandbox"
    PRODUCTION = "production"


@dataclass
class PushToken:
    """One registered token.

    ``registered_at`` is the field the 410 race turns on — see
    ``PushTokenRegistry.purge``.
    """
    token: str
    kind: PushTokenKind
    environment: ApnsEnvironment
    device_id: str
    session_id: Optional[str] = None
    registered_at: float = 0.0
    last_push_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Persisted shape — the token itself is the key, so it is not repeated."""
        return {
            "kind": self.kind.value,
            "environment": self.environment.value,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "registered_at": self.registered_at,
            "last_push_at": self.last_push_at,
        }

    @classmethod
    def from_dict(cls, token: str, data: Dict[str, Any]) -> "PushToken":
        """Rebuild from the persisted shape.

        Every key is read with ``[]``, including the nullable ones: ``to_dict``
        always writes all six, so an absent key is a corrupt record and not an
        old one. Defaulting ``registered_at`` in particular would hand the 410
        race a timestamp of "now" and make the purge guard decide the wrong way.
        A `kind` or `environment` the enums do not know raises ValueError for
        the same reason — it would otherwise be a token nothing ever pushes to.
        """
        return cls(
            token=token,
            kind=PushTokenKind(data["kind"]),
            environment=ApnsEnvironment(data["environment"]),
            device_id=data["device_id"],
            session_id=data["session_id"],
            registered_at=data["registered_at"],
            last_push_at=data["last_push_at"],
        )
