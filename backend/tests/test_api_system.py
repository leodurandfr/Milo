# backend/tests/test_api_system.py
"""
/api/system — the two power actions and the three telemetry reads.

Why this file exists: measured 2026-08-25, `backend/api/system.py` ran at 16.8 %
of its lines, the worst file of `backend/api/`. All nine of its routes were
silent, including `restart_system` and `shutdown_system`.

Nothing here spawns a process. That is the point of the fixture below: two of
these routes shell out (`vcgencmd`, `hostname -I`) and two hand
`SystemdServiceManager.power` to a BackgroundTask that TestClient then runs for
real — and this checkout is the appliance. A spawn that got past a guard here
would reboot the machine running the suite, so the spawn is wired to raise
rather than recorded after the fact (the model is `test_wifi_adoption.py`).

What breaks when these fail:

* the two power routes are the UI's Restart and Shut down buttons, and the
  hostname-conflict screen's only way out. Called inline instead of deferred,
  the box goes down before the response flushes and the UI reports a failure on
  every successful shutdown;
* `/status` and `/recheck-hostname` back the banner that tells an operator two
  Milōs are claiming the same name — the state where neither is reachable by
  name;
* `/network-info` is the IP the settings screen prints. It is what someone types
  when mDNS does not resolve, so an address from the wrong interface is a unit
  that looks unreachable.
"""
import asyncio
import builtins
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api import system as api_system
from backend.api.system import create_system_router


class _Proc:
    """One scripted `vcgencmd`/`hostname` process."""

    def __init__(self, returncode=0, stdout="", hangs=False):
        self.returncode = returncode
        self._out = stdout.encode()
        self._hangs = hangs
        self.killed = False

    # A bounded hang, not an endless one: a mutation that removes the
    # production `wait_for` must make this suite RED, not make it wait — the
    # soft cousin of a mutation that loops instead of failing (T7-1).
    HANG_SECONDS = 2.0

    async def communicate(self):
        if self._hangs:
            await asyncio.sleep(self.HANG_SECONDS)
        return self._out, b""

    def kill(self):
        self.killed = True


@pytest.fixture
def shell(monkeypatch):
    """Every shell spawn explodes until a test scripts one.

    A spy asserted afterwards is too late: `vcgencmd` is harmless, but the guard
    that lets one through is the same guard that lets `sudo systemctl reboot`
    through, and this checkout runs on the appliance.
    """
    scripted = {}

    async def _spawn(command, **kwargs):
        for prefix, proc in scripted.items():
            if command.startswith(prefix):
                return proc
        raise AssertionError(f"an unscripted shell command was spawned: {command!r}")

    monkeypatch.setattr(api_system.asyncio, "create_subprocess_shell", _spawn)
    return scripted


@pytest.fixture
def systemd():
    manager = Mock()
    manager.power = AsyncMock()
    return manager


@pytest.fixture
def hostname_conflict():
    service = Mock()
    service.get_state = Mock(return_value={"hostname_conflict": True, "hostname": "milo-2"})
    service.check = AsyncMock()
    return service


@pytest.fixture
def connectivity():
    service = Mock()
    service.get_state = Mock(return_value={"connectivity": "online"})
    return service


@pytest.fixture
def client(systemd, hostname_conflict, connectivity):
    app = FastAPI()
    app.include_router(
        create_system_router(systemd, hostname_conflict, connectivity),
        prefix="/api/system",
    )
    return TestClient(app)


@pytest.fixture
def timeline(systemd, hostname_conflict, connectivity):
    """A client that records when the response body left, against when the
    power action ran.

    Measured, not assumed: TestClient runs BackgroundTasks itself, so
    `power.assert_awaited_once_with(...)` passes just as well on an inline
    `await` — verified ESCAPED before this fixture existed. Only the ordering
    of the ASGI `http.response.body` message against the call separates the two.
    """
    order = []
    systemd.power = AsyncMock(side_effect=lambda *_: order.append("power"))

    inner = FastAPI()
    inner.include_router(
        create_system_router(systemd, hostname_conflict, connectivity),
        prefix="/api/system",
    )

    async def app(scope, receive, send):
        async def _send(message):
            if message["type"] == "http.response.body" and not message.get("more_body"):
                order.append("response")
            await send(message)
        await inner(scope, receive, _send)

    return TestClient(app), order


# =============================================================================
# The two power actions
# =============================================================================

class TestPower:

    @pytest.mark.parametrize("route,action", [("restart", "reboot"), ("shutdown", "poweroff")])
    def test_the_box_goes_down_after_the_response_and_not_during_it(
        self, client, systemd, route, action
    ):
        """`power()` shells `sudo systemctl reboot|poweroff`.

        The delay is asserted with the verb — it is what lets the response flush
        once the task does run, and a zero there is the same failure as an
        inline call with no symptom on a fast LAN.
        """
        response = client.post(f"/api/system/{route}")

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        systemd.power.assert_awaited_once_with(action, 2.0)

    def test_the_response_leaves_before_the_box_goes_down(self, timeline):
        """Awaited inline, `power` takes the machine down mid-response: the
        browser sees a dropped connection, the UI reports the shutdown failed,
        and the user presses the button again on a box already going down.
        """
        client, order = timeline

        client.post("/api/system/restart")

        assert order == ["response", "power"]

    def test_restart_and_shutdown_do_not_answer_for_each_other(self, client, systemd):
        """Two routes, two literals, one argument apart — and the wrong one
        leaves a speaker off in a house whose owner asked for a reboot.
        """
        client.post("/api/system/restart")
        client.post("/api/system/shutdown")

        assert [c.args[0] for c in systemd.power.await_args_list] == ["reboot", "poweroff"]


# =============================================================================
# Hostname conflict + connectivity
# =============================================================================

class TestSystemStatus:

    def test_status_merges_both_services_over_its_own_defaults(
        self, client, hostname_conflict, connectivity
    ):
        """`systemStore` reads both keys off one response. A merge that drops
        either one leaves the banner permanently at its default — no conflict,
        connectivity unknown — which is exactly what a healthy unit looks like.
        """
        data = client.get("/api/system/status").json()["data"]

        assert data["hostname_conflict"] is True
        assert data["connectivity"] == "online"

    def test_status_answers_without_the_optional_services(self, systemd):
        """Both are injected optionally and a dev host has neither. Failing here
        would take the whole settings screen with it.
        """
        app = FastAPI()
        app.include_router(create_system_router(systemd), prefix="/api/system")

        data = TestClient(app).get("/api/system/status").json()["data"]

        assert data == {"hostname_conflict": False, "connectivity": "unknown"}

    def test_the_manual_recheck_runs_a_check_and_answers_the_new_state(
        self, client, hostname_conflict
    ):
        """The button exists because Avahi's own conflict signal can be missed.
        Answering the cached state without re-checking makes the button a no-op
        that looks like it worked.
        """
        data = client.post("/api/system/recheck-hostname").json()["data"]

        hostname_conflict.check.assert_awaited_once()
        assert data["hostname_conflict"] is True

    def test_the_recheck_is_harmless_without_the_service(self, systemd):
        app = FastAPI()
        app.include_router(create_system_router(systemd), prefix="/api/system")

        response = TestClient(app).post("/api/system/recheck-hostname")

        assert response.status_code == 200
        assert response.json()["data"] == {"hostname_conflict": False}


# =============================================================================
# Temperature
# =============================================================================

class TestTemperature:

    def test_the_reading_is_parsed_out_of_vcgencmd(self, client, shell):
        """`temp=68.1'C` is what the tool on this unit answers, measured
        2026-08-25. The settings screen calls `.toFixed(1)` on whatever comes
        back, so a string would render as a crash, not as a wrong number.
        """
        shell["vcgencmd measure_temp"] = _Proc(stdout="temp=68.1'C\n")

        data = client.get("/api/system/temperature").json()

        assert data["temperature"] == 68.1
        assert isinstance(data["temperature"], float)

    @pytest.mark.parametrize("out,rc", [
        ("", 127),                            # no vcgencmd — a dev host
        ("VCHI initialization failed", 0),
        ("temp=68.1", 0),                     # truncated, no trailing unit
        ("temp=68.1'C", 1),                   # well-formed text, failed exit
    ])
    def test_anything_unparseable_answers_a_null_reading_not_a_failure(
        self, client, shell, out, rc
    ):
        """The screen renders "not available" for null and the error banner for
        a 500. A dev host has no vcgencmd at all, and this route is polled every
        five seconds — a raised failure would be a permanent banner.

        The last case is the one the text alone cannot decide: a reading that
        parses next to a non-zero exit. The exit status is the authority, and
        trusting the text instead is the "success reported over a failed
        subprocess" class that `tests/architecture/test_silent_failure.py`
        exists for.
        """
        shell["vcgencmd measure_temp"] = _Proc(returncode=rc, stdout=out)

        data = client.get("/api/system/temperature").json()

        assert data == {"status": "success", "temperature": None}

    def test_a_hung_vcgencmd_is_killed_rather_than_held(self, client, shell):
        """Without the timeout the request never returns, and the settings
        screen's five-second poll stacks one stuck request on the next.
        """
        proc = _Proc(hangs=True)
        shell["vcgencmd measure_temp"] = proc

        # The production bound is 5 s and nothing here asserts on its value;
        # paying it would put five seconds into the suite for one branch.
        with pytest.MonkeyPatch.context() as mp:
            real_wait_for = asyncio.wait_for
            mp.setattr(
                api_system.asyncio, "wait_for",
                lambda aw, _timeout: real_wait_for(aw, 0.05),
            )
            data = client.get("/api/system/temperature").json()

        assert proc.killed, "the hung process was left running"
        assert data["temperature"] is None


# =============================================================================
# Network info
# =============================================================================

class TestNetworkInfo:

    def test_the_first_ipv4_is_the_one_reported(self, client, shell):
        """`hostname -I` lists every address on the box. Measured on this unit
        it answers eth0, then wlan0, then Tailscale, then the v6 addresses —
        so the first v4 is the LAN address someone can actually type. Reporting
        a v6 or the 100.64/10 Tailscale address instead is a unit that reads as
        unreachable from its own settings screen.
        """
        shell["hostname -I"] = _Proc(stdout=(
            "192.168.1.55 100.117.193.57 2a01:e0a:1048:b5b0:e079:41ff:e835:8628\n"
        ))

        data = client.get("/api/system/network-info").json()

        assert data == {"status": "success", "ip": "192.168.1.55"}

    def test_an_ipv6_only_answer_reports_no_address(self, client, shell):
        """Better than printing an address the user cannot type into a browser
        bar without brackets.
        """
        shell["hostname -I"] = _Proc(stdout="2a01:e0a:1048:b5b0:e079:41ff:e835:8628\n")

        data = client.get("/api/system/network-info").json()

        assert data["status"] == "error"
        assert data["ip"] is None

    @pytest.mark.parametrize("out,rc", [
        ("", 1),
        ("192.168.1.55", 1),   # addresses printed next to a failed exit
    ])
    def test_a_failed_lookup_answers_an_envelope_and_not_a_crash(
        self, client, shell, out, rc
    ):
        """The second case is the one the text alone cannot decide. `hostname`
        writing something on its way to a non-zero exit is the "success reported
        over a failed subprocess" class — the exit status is the authority.
        """
        shell["hostname -I"] = _Proc(returncode=rc, stdout=out)

        data = client.get("/api/system/network-info").json()

        assert data["status"] == "error"
        assert data["ip"] is None


# =============================================================================
# CPU + RAM
# =============================================================================

class TestResources:

    def test_the_two_proc_files_of_this_host_parse_into_a_usable_answer(self, client):
        """Read against the real /proc, deliberately: the shape of `/proc/stat`
        and `/proc/meminfo` belongs to the kernel, and a fixture inventing it
        would agree with the parser rather than with Linux.

        Nothing is asserted against a value this test wrote — only that the two
        extractors produced something the settings screen can draw: a percentage
        it puts in a bar, and a used/total split it prints in MB.
        """
        data = client.get("/api/system/resources").json()

        assert data["status"] == "success"
        assert isinstance(data["cpu_percent"], float)
        assert 0.0 <= data["cpu_percent"] <= 100.0
        assert data["ram"]["total_mb"] > 0
        assert 0 < data["ram"]["used_mb"] < data["ram"]["total_mb"]

    def test_the_percentage_is_the_delta_between_two_snapshots(self, client, monkeypatch):
        """Pure arithmetic, on two snapshots shaped like this host's real
        `/proc/stat` (`cpu` + 10 counters, captured 2026-08-25).

        The real-procfs test above cannot see any of this: every plausible
        misreading of those counters still lands inside 0-100 %. Measured — it
        stayed green against three separate mutations, including reading the
        absolute counters instead of their difference, which reports the average
        load since boot on a screen that says "now".

        First snapshot: idle 700 + iowait 100 of 1000 ticks. Second: idle 1400 +
        iowait 300 of 2000. So 1000 ticks passed, 900 of them idle — 10 % busy.
        """
        snapshots = iter([
            "cpu  100 0 100 700 100 0 0 0 0 0\n",
            "cpu  150 0 150 1400 300 0 0 0 0 0\n",
        ])
        meminfo = (
            "MemTotal:       2048000 kB\n"
            "MemFree:         100000 kB\n"
            "MemAvailable:   1024000 kB\n"
        )
        real_open = builtins.open

        def _fake_proc(file, *args, **kwargs):
            if str(file) == "/proc/stat":
                return io.StringIO(next(snapshots))
            if str(file) == "/proc/meminfo":
                return io.StringIO(meminfo)
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _fake_proc)

        data = client.get("/api/system/resources").json()

        assert data["cpu_percent"] == 10.0
        assert data["ram"] == {"used_mb": 1000, "total_mb": 2000}

    def test_a_host_whose_proc_cannot_be_read_still_answers(self, client, monkeypatch):
        """`InfoSettings.vue` reads `cpu_percent` and `ram` straight off the
        body. A raised failure there is the error banner, permanently, on a
        route polled every five seconds.

        Both readers are refused, not one: they sit in two separate try blocks,
        and breaking a single one leaves the other filling its half of the body.
        """
        real_open = builtins.open

        def _refuse_procfs(file, *args, **kwargs):
            if str(file).startswith("/proc/"):
                raise OSError("procfs is not mounted")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _refuse_procfs)

        data = client.get("/api/system/resources").json()

        assert data == {"status": "success", "cpu_percent": None, "ram": None}
