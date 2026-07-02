"""
Hostname conflict detection for Milō servers.

Two Milō servers on the same LAN both try to claim `milo.local` via mDNS.
The second one is auto-renamed to `milo-2.local` by Avahi, and AirPlay /
Spotify Connect / discovery start showing duplicates. This service detects
that situation so the UI can warn the user.

Detection runs once at backend startup and on demand from the API.
"""
import asyncio
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Set

from backend.core.models.ws_events import SystemHostnameConflictChanged
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)

EXPECTED_SERVER_HOSTNAME = "milo"
EXPECTED_FQDN = f"{EXPECTED_SERVER_HOSTNAME}.local"
RESOLVE_TIMEOUT_S = 3.0
BROWSE_TIMEOUT_S = 5.0
IP_LOCAL_TIMEOUT_S = 2.0
PERIODIC_INTERVAL_S = 300  # 5 minutes
RECLAIM_COOLDOWN_S = 1800  # 30 minutes — avoid restart loops if reclaim fails

# Avahi-renamed Milō servers (milo-2.local, milo-3.local, …) on the LAN —
# distinct from milo-client.local satellites which are legitimate.
RENAMED_MILO_PATTERN = re.compile(r"^milo-\d+\.local$")


class HostnameConflictService:
    """Detects whether another device on the LAN owns `milo.local`."""

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
        self._bg = BackgroundTaskSet(logger, "hostname_conflict")

    def set_state_machine(self, state_machine) -> None:
        self._state_machine = state_machine

    def start_periodic(self) -> None:
        """Start the background check loop (called once after initial check)."""
        if self._periodic_task is not None and not self._periodic_task.done():
            return
        self._periodic_task = asyncio.create_task(self._periodic_loop())

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
          2. Resolve `milo.local`:
             - resolves to one of our IPs → we own it, no conflict.
             - resolves to a remote IP → another device owns it, conflict.
             - doesn't resolve → reverse-resolve our IPs to read our actual
               Avahi name. If Avahi advertises us as `milo-N.local`, we got
               renamed → conflict.
          3. Any unrecoverable error fails open (no conflict).
        """
        should_reclaim = False
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
                        "Hostname conflict: OS hostname is '%s' but Avahi advertises '%s' (expected '%s')",
                        os_hostname, self._advertised_name, EXPECTED_FQDN,
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

            conflict = self._conflict

        if should_reclaim:
            self._bg.spawn(self._attempt_avahi_reclaim(), label="avahi_reclaim")

        return conflict

    async def _detect_conflict(self) -> bool:
        local_ips = await self._get_local_ips()

        resolved_ip = await self._avahi_resolve_name(EXPECTED_FQDN)
        self._milo_local_orphan = resolved_ip is None
        self._other_milos = await self._scan_renamed_milos(local_ips)

        if resolved_ip and resolved_ip in local_ips:
            # We legitimately own milo.local. Conflict only if a renamed
            # Milō server (milo-N.local) is also on the LAN — the user must
            # know to turn it off before it stays orphaned forever.
            self._advertised_name = EXPECTED_FQDN
            self._local_ip = resolved_ip
            return bool(self._other_milos)

        for ip in local_ips:
            if ip == "127.0.0.1":
                continue
            advertised = await self._avahi_resolve_address(ip)
            if advertised:
                self._advertised_name = advertised
                self._local_ip = ip
                if advertised == EXPECTED_FQDN:
                    return False
                return True

        if resolved_ip is not None and resolved_ip not in local_ips:
            self._advertised_name = None
            self._local_ip = self._first_non_loopback(local_ips)
            return True

        self._advertised_name = None
        self._local_ip = self._first_non_loopback(local_ips)
        return False

    @staticmethod
    def _first_non_loopback(local_ips: Set[str]) -> Optional[str]:
        # Sort so the IP shown to the user is deterministic across reboots
        # on dual-iface hosts (eth0 + wlan0). Set iteration order depends
        # on hash randomization.
        candidates = sorted(ip for ip in local_ips if ip != "127.0.0.1")
        return candidates[0] if candidates else None

    @staticmethod
    async def _scan_renamed_milos(local_ips: Set[str]) -> List[str]:
        """Returns the FQDNs of milo-N.local servers visible on the LAN
        (excluding ourselves). Used to detect 'parasite' servers when we
        legitimately own milo.local but another renamed server is lurking.
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
        seen: Set[str] = set()
        out: List[str] = []
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            if not line.startswith("="):
                continue
            fields = line.split(";")
            if len(fields) < 8:
                continue
            fqdn = fields[6]
            ip = fields[7]
            if not RENAMED_MILO_PATTERN.match(fqdn):
                continue
            if ip in local_ips:
                continue
            if fqdn in seen:
                continue
            seen.add(fqdn)
            out.append(fqdn)
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

    async def _attempt_avahi_reclaim(self) -> None:
        logger.warning(
            "Restarting avahi-daemon to reclaim '%s' (currently advertised as '%s', no peer holds it)",
            EXPECTED_FQDN, self._advertised_name,
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
        return await self._run_avahi(["avahi-resolve", "-4", "-n", name])

    async def _avahi_resolve_address(self, ip: str) -> Optional[str]:
        return await self._run_avahi(["avahi-resolve", "-a", ip])

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
        ips: Set[str] = {"127.0.0.1"}
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
