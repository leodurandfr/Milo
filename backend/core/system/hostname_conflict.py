"""
Hostname conflict detection for Milō servers.

Two Milō servers on the same LAN both try to claim `milo.local` via mDNS.
The second one is auto-renamed to `milo-2.local` by Avahi, and AirPlay /
Spotify Connect / discovery start showing duplicates. This service detects
that situation so the UI can warn the user.

The name this unit answers to is read from **Avahi itself**, over the system
bus (`org.freedesktop.Avahi.Server`, readable by any user — no sudoers grant):
`GetHostNameFqdn` is the daemon's own answer to "did I get renamed?", and
`GetState` says whether it has finished probing. Deriving the same fact from
`avahi-resolve` + `ip -4` is what put the full-screen takeover on this
appliance after a plain reboot: the local-address snapshot was taken ~9 s
before the DHCP lease that `avahi-resolve` then answered with, so the unit did
not recognise its own address and reported itself as the intruder. That window
is not exotic — `NetworkManager-wait-online` is masked on purpose
(`install/system.sh`), so the backend starts before any interface has an
address. Measured on this unit 2026-08-31.

Avahi cannot answer one question about itself: whether *another*, already
renamed `milo-N.local` server is sitting on the LAN. That is the browse's only
remaining job.

Detection runs at backend startup, on Avahi's own StateChanged signal, every
five minutes, and on demand from the API.
"""
import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Set

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.core.models.ws_events import SystemHostnameConflictChanged
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)

EXPECTED_SERVER_HOSTNAME = "milo"
EXPECTED_FQDN = f"{EXPECTED_SERVER_HOSTNAME}.local"
RESOLVE_TIMEOUT_S = 3.0
BROWSE_TIMEOUT_S = 5.0
IP_LOCAL_TIMEOUT_S = 2.0
DBUS_TIMEOUT_S = 3.0
PERIODIC_INTERVAL_S = 300  # 5 minutes
RECLAIM_COOLDOWN_S = 1800  # 30 minutes — avoid restart loops if reclaim fails

AVAHI_SERVICE = "org.freedesktop.Avahi"
AVAHI_PATH = "/"
AVAHI_SERVER_IFACE = "org.freedesktop.Avahi.Server"

# AvahiServerState (avahi-common/defs.h). REGISTERING is the boot window, where
# the daemon has not finished probing and nothing about the name is decidable
# yet; COLLISION is the daemon stating the collision itself.
AVAHI_SERVER_REGISTERING = 1
AVAHI_SERVER_RUNNING = 2
AVAHI_SERVER_COLLISION = 3

# Avahi-renamed Milō servers (milo-2.local, milo-3.local, …) on the LAN —
# distinct from milo-client.local satellites which are legitimate.
RENAMED_MILO_PATTERN = re.compile(r"^milo-\d+\.local$")

# RFC 1918 only: 100.64/10 (Tailscale's CGNAT range) reads as private to
# `ipaddress.is_private` but identifies nothing on the LAN.
LAN_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class HostnameConflictService:
    """Detects whether Avahi still publishes this server as `milo.local`."""

    def __init__(self, systemd_manager):
        self._systemd = systemd_manager
        self._state_machine = None
        self._conflict: bool = False
        self._last_checked: Optional[float] = None
        self._advertised_name: Optional[str] = None
        self._local_ip: Optional[str] = None
        self._milo_local_orphan: bool = False
        self._other_milos: List[str] = []
        self._reclaim_attempted_ts: Optional[float] = None
        self._lock = asyncio.Lock()
        self._periodic_task: Optional[asyncio.Task] = None
        self._bus: Optional[MessageBus] = None
        self._server_iface = None
        self._signal_check_pending: bool = False
        self._bg = BackgroundTaskSet(logger, "hostname_conflict")

    def set_state_machine(self, state_machine) -> None:
        self._state_machine = state_machine

    def start_periodic(self) -> None:
        """Start the background check loop (called once after initial check)."""
        if self._periodic_task is not None and not self._periodic_task.done():
            return
        self._periodic_task = asyncio.create_task(self._periodic_loop())

    async def cleanup(self) -> None:
        """Stop the periodic loop, drain the avahi-reclaim tasks, drop the bus."""
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._periodic_task
            self._periodic_task = None
        await self._bg.cancel_all()
        self._release_bus()

    async def _periodic_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(PERIODIC_INTERVAL_S)
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Periodic hostname conflict check crashed: %s", exc)

    def get_state(self) -> Dict[str, Any]:
        return {
            "hostname_conflict": self._conflict,
            "last_checked": self._last_checked,
            "advertised_name": self._advertised_name,
            "local_ip": self._local_ip,
            "expected_name": EXPECTED_FQDN,
            "other_milos": list(self._other_milos),
        }

    async def check(self) -> bool:
        """Run detection. Returns True if a conflict is detected.

        Strategy:
          1. If OS hostname isn't `milo` (e.g. `milo-client`), no conflict.
          2. Ask Avahi what it publishes for us:
             - not RUNNING (still probing, or the daemon is unreachable) →
               nothing is decidable, fail open.
             - COLLISION → the daemon itself reports the name collision.
             - `milo.local` → we own it; conflict only if a renamed Milō
               server is also on the LAN.
             - `milo-N.local` → we lost the name, conflict.
          3. Any unrecoverable error fails open (no conflict).
        """
        should_reclaim = False
        renamed_to: Optional[str] = None
        async with self._lock:
            previous = self._conflict

            os_hostname = socket.gethostname()
            if os_hostname != EXPECTED_SERVER_HOSTNAME:
                self._conflict = False
                self._advertised_name = f"{os_hostname}.local"
                self._last_checked = time.time()
                await self._broadcast_if_changed(previous)
                return False

            try:
                self._conflict = await self._detect_conflict()
            except Exception as exc:
                logger.error("Hostname conflict detection failed: %s", exc)
                self._conflict = False

            self._last_checked = time.time()

            if self._conflict:
                if self._advertised_name == EXPECTED_FQDN and self._other_milos:
                    logger.warning(
                        "Hostname conflict: parasite Milō servers detected on the LAN: %s",
                        ", ".join(self._other_milos),
                    )
                else:
                    logger.warning(
                        "Hostname conflict: Avahi advertises '%s' (expected '%s')",
                        self._advertised_name, EXPECTED_FQDN,
                    )

            await self._broadcast_if_changed(previous)

            # Self-healing: if we got renamed but nobody else owns milo.local,
            # the survivor is just stuck on milo-N — restart Avahi to re-probe.
            # Stamp the cooldown inside the lock, but run the restart outside
            # so we don't block concurrent check() callers (boot init, manual
            # recheck) for up to 10 s.
            if self._should_attempt_reclaim():
                self._reclaim_attempted_ts = time.time()
                should_reclaim = True
                # Carried, not re-read: the restart runs outside the lock, and
                # a concurrent check can have moved the name by then.
                renamed_to = self._advertised_name

            conflict = self._conflict

        if should_reclaim:
            self._bg.spawn(self._attempt_avahi_reclaim(renamed_to), label="avahi_reclaim")

        return conflict

    async def _detect_conflict(self) -> bool:
        # Display only: which of this host's addresses the takeover prints so
        # the owner can tell which box is speaking. No verdict reads it.
        self._local_ip = self._display_address(await self._get_local_ips())
        self._other_milos = []
        self._milo_local_orphan = False

        fqdn, state = await self._avahi_identity()
        self._advertised_name = fqdn
        if fqdn is None:
            return False

        if state == AVAHI_SERVER_COLLISION:
            # The daemon has withdrawn its host records over a collision it saw
            # itself. Not a reclaim case: re-probing against a peer that is
            # still there just loses the race again.
            return True

        if state != AVAHI_SERVER_RUNNING:
            # REGISTERING (the boot window) or a daemon in an unusable state.
            return False

        self._other_milos = self._renamed_peers(await self._browse_workstations(), fqdn)

        if fqdn == EXPECTED_FQDN:
            # We legitimately own milo.local. Conflict only if a renamed Milō
            # server (milo-N.local) is also on the LAN — the user must know to
            # turn it off before it stays orphaned forever.
            return bool(self._other_milos)

        # Avahi renamed us. Whether anybody actually holds milo.local decides
        # the reclaim, not the verdict.
        self._milo_local_orphan = await self._avahi_resolve_name(EXPECTED_FQDN) is None
        return True

    async def _avahi_identity(self) -> tuple:
        """`(fqdn, server state)` as the daemon reports them, `(None, None)` if
        it cannot be reached — which is the fail-open answer, not a conflict."""
        try:
            iface = await self._ensure_server_iface()
            fqdn = await asyncio.wait_for(
                iface.call_get_host_name_fqdn(), timeout=DBUS_TIMEOUT_S
            )
            state = await asyncio.wait_for(
                iface.call_get_state(), timeout=DBUS_TIMEOUT_S
            )
            return fqdn, state
        except Exception as exc:
            # Drop the proxy so the next check rebuilds it: avahi-daemon can be
            # restarted under us (`_attempt_avahi_reclaim` does exactly that),
            # and a connection that failed at boot — the backend starts in the
            # same second as avahi-daemon — must not stay broken until reboot.
            logger.warning(
                "Avahi D-Bus unreachable, hostname conflict undecidable: %s", exc
            )
            self._release_bus()
            return None, None

    async def _ensure_server_iface(self):
        """The `org.freedesktop.Avahi.Server` proxy, connected on first use."""
        if self._server_iface is not None and self._bus is not None and self._bus.connected:
            return self._server_iface

        self._release_bus()
        bus = await asyncio.wait_for(
            MessageBus(bus_type=BusType.SYSTEM).connect(), timeout=DBUS_TIMEOUT_S
        )
        introspection = await asyncio.wait_for(
            bus.introspect(AVAHI_SERVICE, AVAHI_PATH), timeout=DBUS_TIMEOUT_S
        )
        proxy = bus.get_proxy_object(AVAHI_SERVICE, AVAHI_PATH, introspection)
        iface = proxy.get_interface(AVAHI_SERVER_IFACE)
        iface.on_state_changed(self._on_avahi_state_changed)
        self._bus = bus
        self._server_iface = iface
        return iface

    def _release_bus(self) -> None:
        if self._server_iface is not None:
            try:
                self._server_iface.off_state_changed(self._on_avahi_state_changed)
            except Exception as exc:
                logger.debug(f"Avahi signal detach failed: {exc}")
            self._server_iface = None
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception as exc:
                logger.debug(f"Avahi D-Bus disconnect failed: {exc}")
            self._bus = None

    def _on_avahi_state_changed(self, state: int, _error: str) -> None:
        """Avahi settled or collided — re-read now instead of waiting up to five
        minutes for the periodic sweep. A rename fires this signal, which is the
        only way the takeover appears promptly on the unit that lost the race."""
        if state not in (AVAHI_SERVER_RUNNING, AVAHI_SERVER_COLLISION):
            return
        if self._signal_check_pending:
            return
        self._signal_check_pending = True
        self._bg.spawn(self._check_from_signal(), label="avahi_state_changed")

    async def _check_from_signal(self) -> None:
        try:
            await self.check()
        finally:
            self._signal_check_pending = False

    @staticmethod
    def _display_address(local_ips: Set[str]) -> Optional[str]:
        """The address shown in the takeover, LAN first.

        Sorted, and RFC-1918 before anything else, so the answer is stable
        across reboots on a host with several interfaces (eth0 + wlan0 +
        tailscale0 here): set iteration order depends on hash randomisation,
        and a string sort alone hands the owner the Tailscale address —
        `100.…` sorts before `192.…` — which names nothing on their network.
        """
        addresses = []
        for ip in local_ips:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if addr.is_loopback:
                continue
            addresses.append(addr)
        if not addresses:
            return None
        addresses.sort(key=lambda a: (not any(a in net for net in LAN_NETWORKS), a))
        return str(addresses[0])

    @staticmethod
    def _renamed_peers(fqdns: List[str], own_fqdn: str) -> List[str]:
        """The `milo-N.local` servers visible on the LAN, minus ourselves.

        Self-exclusion is by name, which Avahi told us: excluding by address
        used to mean an IPv6 frame could never be recognised as ours (`ip -4`),
        and that a renamed unit could find its own AAAA record among the peers
        and tell its owner to turn itself off.
        """
        seen: Set[str] = set()
        out: List[str] = []
        for fqdn in fqdns:
            if not RENAMED_MILO_PATTERN.match(fqdn):
                continue
            if fqdn == own_fqdn or fqdn in seen:
                continue
            seen.add(fqdn)
            out.append(fqdn)
        return out

    @staticmethod
    async def _browse_workstations() -> List[str]:
        """Every FQDN the LAN's Avahi workstation records carry.

        `publish-workstation=yes` is shipped by `rootfs/etc/avahi/` on the server
        and by `milo-client/rootfs/` on the satellites, so this browse is what
        every Milō on the LAN answers.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "avahi-browse", "-rt", "-p", "_workstation._tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return []
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=BROWSE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return []
        if proc.returncode != 0:
            return []

        # Parsable format: =;<iface>;<proto>;<name>;<type>;<domain>;<fqdn>;<ip>;<port>;<txt>
        out: List[str] = []
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            if not line.startswith("="):
                continue
            fields = line.split(";")
            if len(fields) < 7:
                continue
            out.append(fields[6])
        return out

    def _should_attempt_reclaim(self) -> bool:
        """True iff we got renamed but nobody else owns milo.local on the LAN.

        Avahi never reclaims the principal name once it has been renamed —
        even after the conflicting peer disappears. A restart of avahi-daemon
        forces it to re-probe milo.local. Cooldown avoids restart loops if the
        reclaim doesn't take (e.g. another race on the next probe).
        """
        if not self._conflict:
            return False
        if not self._milo_local_orphan:
            return False
        if self._advertised_name is None or self._advertised_name == EXPECTED_FQDN:
            return False
        if self._reclaim_attempted_ts is not None:
            elapsed = time.time() - self._reclaim_attempted_ts
            if elapsed < RECLAIM_COOLDOWN_S:
                return False
        return True

    async def _attempt_avahi_reclaim(self, renamed_to: Optional[str]) -> None:
        logger.warning(
            "Restarting avahi-daemon to reclaim '%s' (advertised as '%s', no peer holds it)",
            EXPECTED_FQDN, renamed_to,
        )
        # restart() logs + returns False on failure (fail-loud); the caller can't
        # act on a failed reclaim beyond the warning above, so the bool is unused.
        await self._systemd.restart("avahi-daemon")

    async def _broadcast_if_changed(self, previous: bool) -> None:
        if previous == self._conflict or self._state_machine is None:
            return
        await self._state_machine.broadcast(SystemHostnameConflictChanged(
            hostname_conflict=self._conflict,
            advertised_name=self._advertised_name,
            local_ip=self._local_ip,
            expected_name=EXPECTED_FQDN,
        ))

    async def _avahi_resolve_name(self, name: str) -> Optional[str]:
        """Does anybody at all answer for `name`? Only the reclaim reads this."""
        return await self._run_avahi(["avahi-resolve", "-4", "-n", name])

    @staticmethod
    async def _run_avahi(cmd) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return None
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=RESOLVE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0:
            return None
        # Output format: "<query>\t<answer>"
        parts = stdout.decode("utf-8", errors="ignore").strip().split()
        return parts[1] if len(parts) >= 2 else None

    @staticmethod
    async def _get_local_ips() -> Set[str]:
        """This host's IPv4 addresses. Display only — see `_display_address`."""
        ips: Set[str] = set()
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-4", "-o", "addr", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return ips
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=IP_LOCAL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ips
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            tokens = line.split()
            try:
                cidr = tokens[tokens.index("inet") + 1]
                ips.add(cidr.split("/")[0])
            except (ValueError, IndexError):
                continue
        return ips
