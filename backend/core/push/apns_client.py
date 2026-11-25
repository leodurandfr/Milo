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

logger = logging.getLogger(__name__)

HOSTS = {
    ApnsEnvironment.SANDBOX: "https://api.sandbox.push.apple.com",
    ApnsEnvironment.PRODUCTION: "https://api.push.apple.com",
}

# APNs rejects a provider token older than 1h (ExpiredProviderToken) and
# rejects minting them too often (TooManyProviderTokenUpdates). Anything in
# 20..60 min satisfies both; 50 leaves room for a slow request at the edge.
JWT_LIFETIME_S = 50 * 60

# systemd-timesyncd's mark of a first synchronization since boot. Until it is
# there the wall clock is the one the card last saved, and a provider token
# dated from it is already older than APNs' hour: measured 2026-11-25, a unit
# booted at 11:48 read 10:08 until 11:52, and every push in between answered
# 403 ExpiredProviderToken — an error banner for a clock nobody had set yet.
# A host with no timesyncd (a dev host) has no directory, and counts as set.
TIMESYNC_DIR = Path("/run/systemd/timesync")

# How long an idle connection is kept for the next push. httpx's default is
# 5 s, and httpcore retires an expired connection at the next request, so every
# push after a quiet spell paid TCP + TLS before its own round trip: 0.33-0.45 s
# to APNs' 200 instead of 0.10-0.15 s (measured 2026-09-26). Apple closes an
# idle connection itself after two hours; half an hour stays well inside that.
# A connection broken in silence costs one push: httpcore marks it errored on
# the failed read and opens a fresh one for the next.
KEEPALIVE_S = 30 * 60

# Reason codes that mean "this token will never work again". Everything else
# is transient or a bug on this side, and must NOT cost the caller its token.
DEAD_TOKEN_REASONS = {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic", "ExpiredToken"}

# The dead reasons that mean the token was registered wrong rather than that it
# expired: sent to the host or the app its registration named, Apple does not
# know it. A token that dies this way was never reachable, so every push to it
# was lost — logged at warning, with what the registration claimed, so the
# lying field shows up in errors.log. Measured 2026-11-02: Milo-iOS declared
# its Now Playing tokens `production` after iOS 27.2 had made them sandbox, and
# each was purged on its first push for three days, at info, unseen.
MISREGISTERED_TOKEN_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic"}

# The refusals that say something about this unit rather than about one push:
# a key Apple does not trust (revoked, or not of APNS_TEAM_ID's team) and a push
# type it does not know. They hold until the owner changes something, and each
# push meets them again — every cycle, and every retry the service spaces. Since
# this logger sits under `backend`, an ERROR is a banner on every screen, so one
# is written per episode and the episode ends at the next push Apple takes.
DURABLE_REFUSALS = {"InvalidProviderToken", "InvalidPushType"}

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


class ApnsClient:
    """Mints provider tokens and posts payloads. Holds no policy about when."""

    def __init__(self):
        self.key_dir: Path = APNS_KEY_DIR
        self.timesync_dir: Path = TIMESYNC_DIR
        # Once set, the clock stays set for the boot: read until then only.
        self._clock_set: bool = False
        self._clock_wait_logged: bool = False
        # The durable refusals already said, per push type: their episode.
        # See DURABLE_REFUSALS.
        self._refusals_said: set = set()
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
            return ApnsResult(ok=False, status=0, reason="NoSigningKey")
        # Not dead: a push refused here is one the caller may send again.
        if not await self.clock_synchronized():
            return ApnsResult(ok=False, status=0, reason="ClockNotSet")

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

        result = self._interpret(response, push_type)
        if result.reason in MISREGISTERED_TOKEN_REASONS:
            logger.warning(
                f"APNs refused a {target.kind.value} token as registered "
                f"({target.environment.value}, device {target.device_id}, "
                f"{target.token[:8]}…): {result.reason} — check the environment "
                f"the app declares; it will be purged"
            )
        return result

    def _interpret(self, response: httpx.Response, push_type: str) -> ApnsResult:
        """Turn an APNs response into a verdict, and log what an operator needs.

        A 200 means Apple accepted the request for delivery. It does not mean
        the device displayed anything — a payload iOS cannot decode is dropped
        on the phone, silently, and looks exactly like this.

        What reaches ERROR — a banner, this logger being under `backend` — is
        what asks the owner to act, and a durable refusal says so once per
        episode (DURABLE_REFUSALS). Apple's own trouble — a 429, a 5xx — is
        what the service sends again, spaced: at warning, kept in errors.log,
        a banner per retry for a fault no gesture here corrects.
        """
        apns_id = response.headers.get("apns-id", "")
        if response.status_code == 200:
            # The key is trusted and this push type known: their episodes end.
            self._refusals_said -= {("InvalidProviderToken", None), ("InvalidPushType", push_type)}
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
        if dead and reason not in MISREGISTERED_TOKEN_REASONS:
            logger.info(f"APNs reports a token unusable ({reason}) — it will be purged")
        elif reason in DURABLE_REFUSALS:
            # The key is shared by every push type; a push type is its own.
            episode = (reason, push_type if reason == "InvalidPushType" else None)
            if episode in self._refusals_said:
                logger.debug(f"APNs refused a {push_type} push again: {reason}")
            elif reason == "InvalidProviderToken":
                self._refusals_said.add(episode)
                logger.error(
                    f"APNs refused the signing key {self._key_id}: it is not trusted for "
                    f"team {APNS_TEAM_ID}. Note this is checked BEFORE the push type, so "
                    f"it says nothing about the payload. Said once, until a push is taken."
                )
            else:
                self._refusals_said.add(episode)
                logger.error(
                    f"APNs does not know the push type {push_type!r}. Measured "
                    "accepted: widgets, nowplaying, liveactivity. Said once, until "
                    "a push of that type is taken."
                )
        elif response.status_code == 429 or response.status_code >= 500:
            logger.warning(
                f"APNs could not take a {push_type} push now: HTTP "
                f"{response.status_code} {reason} — it is sent again, spaced"
            )
        else:
            logger.error(f"APNs refused a push: HTTP {response.status_code} {reason}")

        return ApnsResult(
            ok=False, status=response.status_code, reason=reason,
            apns_id=apns_id, dead=dead, invalidated_at=invalidated_at,
        )

    # =========================================================================
    # CLOCK
    # =========================================================================

    async def clock_synchronized(self) -> bool:
        """Whether the wall clock a provider token is dated from has been set
        (TIMESYNC_DIR). Said once at info while it has not.

        Public because a push held here is not a refusal: PushService asks
        first, and while the clock is unset it tries nothing and counts
        nothing (`PushService._clock_held`). `send` still answers ClockNotSet
        to a caller that did not ask."""
        if self._clock_set:
            return True
        directory = self.timesync_dir
        synchronized = await asyncio.to_thread(
            lambda: not directory.is_dir() or (directory / "synchronized").exists()
        )
        if synchronized:
            # Two sends can read it across the same await: one says it.
            if not self._clock_set and self._clock_wait_logged:
                logger.info("Wall clock synchronized: pushes go out again")
            self._clock_set = True
        elif not self._clock_wait_logged:
            self._clock_wait_logged = True
            logger.info("Pushes held until the wall clock is synchronized (a token dated before would be expired)")
        return synchronized

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
        Apple counts against the connection budget. Hence `KEEPALIVE_S` rather
        than httpx's five seconds, which reopened it for nearly every push.
        """
        async with self._clients_lock:
            if environment not in self._clients:
                self._clients[environment] = httpx.AsyncClient(
                    base_url=HOSTS[environment], http2=True, timeout=10.0,
                    # Leaves the connection counts uncapped, which is moot:
                    # a handful of tokens share one HTTP/2 connection per host.
                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S),
                )
            return self._clients[environment]
