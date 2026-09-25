# backend/core/push/apns_client.py
"""Signed HTTP/2 delivery of one payload to one APNs device token.

What is measured rather than assumed (2026-09-19, against the real service
from this unit, key 283578Y5GK):

* The provider connection is **outbound**. A LAN-only appliance reaches
  api.push.apple.com with no port opened, no tunnel and no dynamic DNS.
* APNs validates in a fixed order: **provider token, then push type, then
  device token.** An untrusted key answers `InvalidProviderToken` whatever the
  push type — `apns-push-type: banana` and `widgets` are indistinguishable
  until the key is trusted. With a trusted key, `banana` answers
  `InvalidPushType` while a real type reaches the token check. That ordering is
  why `_interpret` never reports "the payload was wrong" from a 403.
* `nowplaying`, `liveactivity` and `widgets` are all accepted push types.
* The **topic is not validated** against a bad device token: a topic naming an
  app that does not exist still answers `BadDeviceToken`. So a wrong topic is
  invisible here and only shows up as a push that silently reaches nobody.

Fails open, like every other external dependency in this codebase: with no
signing key on the unit this logs once and reports every send as skipped, so a
dev host runs with no Apple account at all.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import jwt

from backend.config.constants import APNS_KEY_DIR, APNS_TEAM_ID, IOS_APP_BUNDLE_ID
from backend.core.push.models import ApnsEnvironment, PushToken

logger = logging.getLogger("core.push.apns")

HOSTS = {
    ApnsEnvironment.SANDBOX: "https://api.sandbox.push.apple.com",
    ApnsEnvironment.PRODUCTION: "https://api.push.apple.com",
}

# APNs rejects a provider token older than 1h (ExpiredProviderToken) and
# rejects minting them too often (TooManyProviderTokenUpdates). Anything in
# 20..60 min satisfies both; 50 leaves room for a slow request at the edge.
JWT_LIFETIME_S = 50 * 60

# Reason codes that mean "this token will never work again". Everything else
# is transient or a bug on this side, and must NOT cost the caller its token.
DEAD_TOKEN_REASONS = {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic", "ExpiredToken"}

_KEY_FILENAME = re.compile(r"AuthKey_([A-Z0-9]{10})\.p8")


@dataclass
class ApnsResult:
    """What one send did. `dead` is the only field the caller must act on."""
    ok: bool
    status: int
    reason: str = ""
    apns_id: str = ""
    dead: bool = False
    # Seconds since the epoch, from a 410 body. APNs sends milliseconds; the
    # division happens here so PushTokenRegistry.purge only ever sees seconds.
    invalidated_at: Optional[float] = None
    skipped: bool = False


class ApnsClient:
    """Mints provider tokens and posts payloads. Holds no policy about when."""

    def __init__(self):
        self.key_dir: Path = APNS_KEY_DIR
        self._key_pem: Optional[str] = None
        self._key_id: Optional[str] = None
        self._jwt: Optional[str] = None
        self._jwt_minted_at: float = 0.0
        self._jwt_lock = asyncio.Lock()
        self._clients: dict = {}
        self._clients_lock = asyncio.Lock()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def initialize(self) -> None:
        """Locate the signing key. A missing key is not an error.

        The key is provisioned per device and deliberately absent from the
        image — it signs for every app in the Apple team, so baking it into
        pi-gen would ship one credential on every card. A unit without it runs
        normally and pushes nothing.
        """
        self._load_key()

    def _load_key(self) -> None:
        found = sorted(self.key_dir.glob("AuthKey_*.p8")) if self.key_dir.is_dir() else []
        if not found:
            logger.info(
                f"No APNs signing key in {self.key_dir} — push delivery is off. "
                "Drop AuthKey_<KEYID>.p8 there (milo:milo, 0600) to enable it."
            )
            return
        if len(found) > 1:
            logger.error(
                f"Several APNs keys in {self.key_dir}: {[f.name for f in found]} — "
                f"push delivery is off until exactly one remains"
            )
            return

        match = _KEY_FILENAME.fullmatch(found[0].name)
        if not match:
            logger.error(
                f"APNs key {found[0].name} is not named AuthKey_<KEYID>.p8 — the "
                "filename is where the Key ID comes from, so it cannot be used"
            )
            return

        self._key_pem = found[0].read_text()
        self._key_id = match.group(1)
        logger.info(f"APNs signing key {self._key_id} loaded")

    async def cleanup(self) -> None:
        """Close the pooled HTTP/2 connections."""
        async with self._clients_lock:
            for client in self._clients.values():
                await client.aclose()
            self._clients.clear()

    @property
    def available(self) -> bool:
        """False when no key is installed — the caller skips instead of trying."""
        return self._key_pem is not None

    # =========================================================================
    # SEND
    # =========================================================================

    async def send(self, target: PushToken, payload: dict, push_type: str,
                   priority: int = 10) -> ApnsResult:
        """POST one payload to one token, on the host that token belongs to.

        The host comes from the token's own recorded environment, never from a
        unit-wide setting: one phone can hold a Debug token and a TestFlight
        token at the same time, and sending either to the wrong host answers
        BadDeviceToken — which is indistinguishable from a malformed token.
        """
        if not self.available:
            return ApnsResult(ok=False, status=0, reason="NoSigningKey", skipped=True)

        client = await self._client_for(target.environment)
        try:
            response = await client.post(
                f"/3/device/{target.token}",
                json=payload,
                headers={
                    "authorization": f"bearer {await self._provider_token()}",
                    "apns-topic": f"{IOS_APP_BUNDLE_ID}.push-type.{push_type}",
                    "apns-push-type": push_type,
                    "apns-priority": str(priority),
                },
            )
        except httpx.HTTPError as e:
            # Fail open: Apple unreachable is not this appliance's failure, and
            # it must never cost a token. A purge here would empty the registry
            # over a router reboot.
            logger.warning(f"APNs unreachable ({type(e).__name__}): {e}")
            return ApnsResult(ok=False, status=0, reason="Unreachable")

        return self._interpret(response)

    def _interpret(self, response: httpx.Response) -> ApnsResult:
        """Turn an APNs response into a verdict, and log what an operator needs.

        A 200 means Apple accepted the request for delivery. It does not mean
        the device displayed anything — a payload iOS cannot decode is dropped
        on the phone, silently, and looks exactly like this.
        """
        apns_id = response.headers.get("apns-id", "")
        if response.status_code == 200:
            return ApnsResult(ok=True, status=200, apns_id=apns_id)

        try:
            body = response.json()
        except ValueError:
            body = {}
        reason = body.get("reason", "")

        invalidated_at = None
        if "timestamp" in body:
            # 410 carries it in MILLISECONDS. Feeding that straight to purge()
            # would compare milliseconds against seconds and keep every dead
            # token forever, which is the bug the guard exists to prevent.
            invalidated_at = body["timestamp"] / 1000

        dead = reason in DEAD_TOKEN_REASONS
        if dead:
            logger.info(f"APNs reports a token unusable ({reason}) — it will be purged")
        elif reason == "InvalidProviderToken":
            logger.error(
                f"APNs refused the signing key {self._key_id}: it is not trusted for "
                f"team {APNS_TEAM_ID}. Note this is checked BEFORE the push type, so "
                f"it says nothing about the payload."
            )
        elif reason == "InvalidPushType":
            logger.error(
                "APNs does not know this push type. Measured accepted: widgets, "
                "nowplaying, liveactivity."
            )
        else:
            logger.error(f"APNs refused a push: HTTP {response.status_code} {reason}")

        return ApnsResult(
            ok=False, status=response.status_code, reason=reason,
            apns_id=apns_id, dead=dead, invalidated_at=invalidated_at,
        )

    # =========================================================================
    # PRIVATE
    # =========================================================================

    async def _provider_token(self) -> str:
        """The cached ES256 JWT, reminted only when it is close to expiring.

        Minting one per push is what earns TooManyProviderTokenUpdates, and
        that throttle applies to the whole app rather than to one connection.
        """
        async with self._jwt_lock:
            if self._jwt and (time.time() - self._jwt_minted_at) < JWT_LIFETIME_S:
                return self._jwt
            self._jwt = jwt.encode(
                {"iss": APNS_TEAM_ID, "iat": int(time.time())},
                self._key_pem,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            self._jwt_minted_at = time.time()
            logger.debug(f"Minted an APNs provider token with key {self._key_id}")
            return self._jwt

    async def _client_for(self, environment: ApnsEnvironment) -> httpx.AsyncClient:
        """One pooled HTTP/2 client per host, created on first use.

        Reusing the connection is the point: APNs expects a provider to hold
        one open, and a fresh TLS handshake per push is both slow and something
        Apple counts against the connection budget.
        """
        async with self._clients_lock:
            if environment not in self._clients:
                self._clients[environment] = httpx.AsyncClient(
                    base_url=HOSTS[environment], http2=True, timeout=10.0,
                )
            return self._clients[environment]
