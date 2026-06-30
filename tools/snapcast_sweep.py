#!/usr/bin/env python3
"""Snapcast configuration sweep — find the lowest-latency config with zero audio loss.

This is a DEV / calibration tool, not part of the shipped appliance. It drives the
real backend API (the same path the settings UI uses) to apply a grid of Snapcast
server configs, plays a sustained test signal through the production audio pipeline,
and measures audio loss objectively.

Why this works without a microphone: Snapcast never loses audio silently. When a
client can't serve a chunk on time it logs a buffer underrun / hard resync, and ALSA
logs an xrun. So "zero underrun/xrun events over a long window" is a strong objective
guarantee that the audio came out clean. We collect those events from journald:
  - local snapclient  : journalctl on this machine (milo-snapclient-multiroom.service)
  - remote snapclients : ssh <ip> journalctl (milo-client-snapclient.service)
There is no HTTP endpoint exposing remote underruns, so remotes need SSH key access
from this host. Remotes that aren't SSH-reachable are reported as "connection-only"
(we still watch their RPC connection stability) and never counted as a silent pass.

What it does NOT measure: absolute end-to-end latency in milliseconds. For the
lip-sync use case, measure that once, by hand, on the 1-2 winning configs (film the
screen + speaker in slow-motion, or record source + speaker in Audacity and read the
offset). The sweep finds the lowest STABLE buffer; absolute latency ~= buffer + a
small constant you confirm physically.

Usage:
  # Preflight only (no config change): check API/RPC reachability, discover clients,
  # test SSH + journald access on every remote, print the planned grid + time estimate.
  python tools/snapcast_sweep.py --source-url http://stream.example/test.flac --dry-run

  # Real sweep, 10-minute window per config:
  python tools/snapcast_sweep.py --station-id <favorite-id> --window 600

  # Custom grid from a JSON file (array of config dicts):
  python tools/snapcast_sweep.py --source-url file:///var/lib/milo/test.flac --grid grid.json

The original server config is read at startup and restored on exit (including Ctrl-C).
"""
import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --- Wire contract (see backend/core/multiroom/routes.py + snapcast.py) ---
API_GET_CONFIG = "/api/routing/snapcast/server-config"   # GET  -> {config, capabilities}
API_SET_CONFIG = "/api/routing/snapcast/server/config"   # POST {config: {...}}
API_RADIO_PLAY = "/api/radio/play"
API_RADIO_STOP = "/api/radio/stop"
RPC_PATH = "/jsonrpc"                                     # Server.GetStatus

LOCAL_SNAPCLIENT_UNIT = "milo-snapclient-multiroom.service"
REMOTE_SNAPCLIENT_UNIT = "milo-client-snapclient.service"

# Config validation mirrors snapcast.py::_validate_config so we fail fast client-side.
VALIDATORS = {
    "buffer_ms": lambda x: isinstance(x, int) and 80 <= x <= 3000,
    "codec": lambda x: x in ("flac", "pcm", "opus", "ogg"),
    "chunk_ms": lambda x: isinstance(x, int) and 15 <= x <= 50,
    "snapclient_buffer_time": lambda x: isinstance(x, int) and 60 <= x <= 300,
    "snapclient_fragments": lambda x: isinstance(x, int) and 2 <= x <= 8,
}
CONFIG_KEYS = list(VALIDATORS.keys())

# Default grid: tuned for the all-Ethernet + lip-sync scenario. Find the buffer floor
# with the lowest-latency codec (pcm = lossless, zero encode/decode, fine on wired),
# then keep one flac reference for comparison. Override with --grid.
DEFAULT_GRID = [
    {"buffer_ms": 400, "codec": "flac", "chunk_ms": 40, "snapclient_buffer_time": 100},  # safe reference
    {"buffer_ms": 300, "codec": "pcm", "chunk_ms": 20, "snapclient_buffer_time": 80},
    {"buffer_ms": 250, "codec": "pcm", "chunk_ms": 20, "snapclient_buffer_time": 60},
    {"buffer_ms": 200, "codec": "pcm", "chunk_ms": 20, "snapclient_buffer_time": 60},
    {"buffer_ms": 250, "codec": "flac", "chunk_ms": 20, "snapclient_buffer_time": 60},   # codec A/B at the floor
]

# Events that mean real audio loss, calibrated against the shipped snapclient build's
# own vocabulary: "(Stream) No chunks available", "(Alsa) Failed to get chunk",
# "(Alsa) No chunk received for Nms". These also fire benignly on idle/stop
# transitions, which is why measurement is gated on stream==playing and counted only
# from t0 (after audio start). Periodic "soft sync" clock nudges are NORMAL and
# excluded. Tune with --underrun-pattern after inspecting the printed sample lines.
DEFAULT_UNDERRUN_PATTERN = (
    r"no chunks? (available|received)|failed to get chunk|"
    r"underrun|xrun|hard.?sync|too late|dropout|drop.?out|"
    r"buffer too small|failed to (get|send)|missed|stutter"
)


# ----------------------------------------------------------------------------- HTTP

def _http_json(url, method="GET", body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def get_config(self):
        return _http_json(self.base + API_GET_CONFIG)

    def set_config(self, config):
        return _http_json(self.base + API_SET_CONFIG, method="POST", body={"config": config}, timeout=60)

    def radio_play(self, station_id, station=None):
        body = {"station_id": station_id}
        if station:
            body["station"] = station
        return _http_json(self.base + API_RADIO_PLAY, method="POST", body=body)

    def radio_stop(self):
        try:
            return _http_json(self.base + API_RADIO_STOP, method="POST")
        except urllib.error.URLError:
            return None

    def change_source(self, name):
        return _http_json(self.base + f"/api/audio/source/{name}", method="POST", timeout=30)

    def audio_state(self):
        return _http_json(self.base + "/api/audio/state")


class Rpc:
    """Snapcast JSON-RPC (Server.GetStatus) — the source of truth for client
    connection state and whether audio is actually flowing into the server."""

    def __init__(self, host, port):
        self.url = f"http://{host}:{port}{RPC_PATH}"
        self._id = 0

    def status(self):
        self._id += 1
        body = {"id": self._id, "jsonrpc": "2.0", "method": "Server.GetStatus"}
        resp = _http_json(self.url, method="POST", body=body, timeout=5)
        return resp.get("result", {}).get("server", {})

    @staticmethod
    def _norm_ip(ip):
        # Snapserver reports IPv6-mapped IPv4 (e.g. "::ffff:192.168.1.153").
        return ip[7:] if ip.startswith("::ffff:") else ip

    @staticmethod
    def clients(server):
        out = []
        for group in server.get("groups", []):
            for c in group.get("clients", []):
                host = c.get("host", {})
                out.append({
                    "id": c.get("id"),
                    "mac": host.get("mac", ""),
                    "ip": Rpc._norm_ip(host.get("ip", "")),
                    "name": host.get("name", "") or c.get("config", {}).get("name", ""),
                    "connected": bool(c.get("connected", False)),
                })
        return out

    @staticmethod
    def is_playing(server):
        return any(s.get("status") == "playing" for s in server.get("streams", []))


# ------------------------------------------------------------------------- journald

def _run(cmd, timeout, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError as e:
        return 127, "", str(e)


def journal_local(unit, since_epoch, use_sudo):
    cmd = (["sudo"] if use_sudo else []) + [
        "journalctl", "-u", unit, "--since", f"@{since_epoch}", "--no-pager", "-o", "cat",
    ]
    rc, out, err = _run(cmd, timeout=30)
    return rc, out, err


def journal_remote(ip, ssh_user, unit, since_epoch, remote_sudo, askpass=None):
    remote = (("sudo -n " if remote_sudo else "")
              + f"journalctl -u {unit} --since @{since_epoch} --no-pager -o cat")
    if askpass:
        # Transient password auth via SSH_ASKPASS — no persistent key on the remote.
        cmd = ["setsid", "-w", "ssh", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=5", f"{ssh_user}@{ip}", remote]
        env = {**os.environ, "SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force"}
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
               f"{ssh_user}@{ip}", remote]
        env = None
    rc, out, err = _run(cmd, timeout=30, env=env)
    return rc, out, err


def count_underruns(text, pattern):
    matches = [ln for ln in text.splitlines() if pattern.search(ln)]
    return len(matches), matches


# ---------------------------------------------------------------------------- sweep

class Sweep:
    def __init__(self, args, api, rpc):
        self.args = args
        self.api = api
        self.rpc = rpc
        self.pattern = re.compile(args.underrun_pattern, re.IGNORECASE)
        self.original = None
        self.original_source = None
        self.remotes = []       # [{ip, name, observable}]
        self.expected_macs = set()
        self.results = []

    # -- setup / teardown ----------------------------------------------------

    def read_original(self):
        r = self.api.get_config()
        cfg = (r or {}).get("config")
        if not cfg:
            raise SystemExit(f"Cannot read current config (is multiroom enabled?): {r}")
        sc = cfg.get("stream_config", {})
        self.original = {
            "buffer_ms": int(sc["buffer_ms"]),
            "codec": sc["codec"],
            "chunk_ms": int(sc["chunk_ms"]),
            "snapclient_buffer_time": int(cfg.get("snapclient_buffer_time", 80)),
        }
        try:
            self.original_source = (self.api.audio_state() or {}).get("active_source")
        except Exception:
            self.original_source = None
        return self.original

    def restore(self):
        if not self.original:
            return
        print(f"\n[restore] reapplying original config: {self.original}")
        try:
            resp = self.api.set_config(dict(self.original))
            if (resp or {}).get("status") == "success":
                print("[restore] done.")
            else:  # HTTP 200 with status:error is a logical failure, not an exception
                print(f"[restore] WARNING: server returned {resp}. Re-apply manually from the "
                      f"settings UI:\n          {self.original}")
        except Exception as e:  # best-effort; the operator can re-apply from the UI
            print(f"[restore] FAILED ({e}). Re-apply manually from the settings UI:\n"
                  f"          {self.original}")
        if self.original_source and self.original_source != "none":
            try:
                self.api.change_source(self.original_source)
                print(f"[restore] active source -> {self.original_source}")
            except Exception as e:
                print(f"[restore] could not restore source {self.original_source}: {e}")

    # -- discovery -----------------------------------------------------------

    def discover(self):
        server = self.rpc.status()
        clients = self.rpc.clients(server)
        if not clients:
            raise SystemExit(
                "No Snapcast clients reported by the server.\n"
                "  Before sweeping: enable multiroom in the UI (the local client connects via\n"
                "  127.0.0.1) and power on the remote clients you want measured, then re-run.")
        self.expected_macs = {c["mac"] for c in clients if c["mac"]}
        for c in clients:
            if c["ip"] in ("127.0.0.1", "::1", ""):
                continue
            self.remotes.append({"ip": c["ip"], "name": c["name"] or c["ip"], "observable": False})
        return clients

    def probe_remote_observability(self):
        """Test SSH + journald access per remote so the sweep declares up front
        whether each remote will be log-observed or connection-only."""
        now = int(time.time())
        for r in self.remotes:
            rc, out, err = journal_remote(r["ip"], self.args.ssh_user, REMOTE_SNAPCLIENT_UNIT,
                                          now - 5, self.args.remote_sudo, self.args.ssh_askpass)
            r["observable"] = (rc == 0)
            status = "log-observed" if rc == 0 else f"CONNECTION-ONLY (ssh rc={rc}: {err.strip()[:80]})"
            print(f"  remote {r['name']:<20} {r['ip']:<16} -> {status}")

    def probe_local_observability(self):
        now = int(time.time())
        rc, out, err = journal_local(LOCAL_SNAPCLIENT_UNIT, now - 5, self.args.sudo)
        ok = rc == 0
        print(f"  local  snapclient        journald     -> "
              f"{'readable' if ok else f'UNREADABLE (rc={rc}: {err.strip()[:80]})'}")
        return ok

    # -- per-config measurement ---------------------------------------------

    def apply_and_settle(self, config):
        print(f"  applying {config} ...")
        resp = self.api.set_config(dict(config))
        if (resp or {}).get("status") != "success":
            print(f"  WARN apply did not report success: {resp}")
        # Wait for all expected clients to reconnect to the new server.
        deadline = time.time() + self.args.settle_timeout
        while time.time() < deadline:
            with contextlib.suppress(Exception):
                server = self.rpc.status()
                connected = {c["mac"] for c in self.rpc.clients(server) if c["connected"]}
                if self.expected_macs and self.expected_macs.issubset(connected):
                    print(f"  settled: {len(connected)}/{len(self.expected_macs)} clients connected")
                    return True
            time.sleep(2)
        print("  WARN not all clients reconnected before settle timeout — measuring anyway")
        return False

    def activate_radio(self):
        """Make radio the active source once, so its mpv is connected and feeds the
        Radio ALSA loopback that snapserver captures into the Multiroom meta-stream."""
        print("  activating radio source for the test signal ...")
        try:
            self.api.change_source("radio")
            time.sleep(2)
        except Exception as e:
            print(f"  WARN could not activate radio source: {e}")

    def start_audio(self):
        if self.args.station_id:
            self.api.radio_play(self.args.station_id)
        else:
            url = self.args.source_url
            self.api.radio_play("sweep-test", {"url": url, "url_resolved": url, "name": "sweep-test"})

    def monitor(self, config):
        """Watch one window in real time. Returns a result row."""
        self.start_audio()
        # Wait for playback to actually resume, then let the post-restart reconnect /
        # ALSA-reopen transition pass BEFORE opening the measurement window, so the
        # one-shot reconnect event isn't miscounted as a mid-stream underrun.
        stab_deadline = time.time() + self.args.stabilize + 8
        while time.time() < stab_deadline:
            with contextlib.suppress(Exception):
                if self.rpc.is_playing(self.rpc.status()):
                    break
            time.sleep(1)
        time.sleep(self.args.stabilize)
        t0 = int(time.time())

        window = self.args.window
        seen_connected = {}     # mac -> currently connected?
        flips = {}              # mac -> drop count
        stream_stalled = False
        end = t0 + window
        while time.time() < end:
            try:
                server = self.rpc.status()
                playing = self.rpc.is_playing(server)
                if not playing:
                    stream_stalled = True
                conn = {c["mac"]: c["connected"] for c in self.rpc.clients(server)}
                for mac, up in conn.items():
                    if seen_connected.get(mac) and not up:
                        flips[mac] = flips.get(mac, 0) + 1
                    seen_connected[mac] = up
                n_up = sum(1 for v in conn.values() if v)
            except Exception:
                playing, n_up = None, "?"

            local_n, _, _ = self._local_underruns(t0)
            rem_total = self._remote_underruns_total(t0)
            elapsed = int(time.time()) - t0
            print(f"    [{elapsed:>4}s/{window}s] clients={n_up}/{len(self.expected_macs)} "
                  f"stream={'play' if playing else 'IDLE' if playing is not None else '?'} "
                  f"underruns: local={local_n} remotes={rem_total}"
                  + ("  <-- STREAM STALLED" if stream_stalled else ""),
                  flush=True)
            time.sleep(self.args.poll)

        # Final authoritative counts over the whole window.
        local_n, local_lines, local_ok = self._local_underruns(t0)
        remote_counts = self._remote_underruns_detail(t0)
        total_flips = sum(flips.values())

        observed_remote_total = sum(v for v in remote_counts.values() if isinstance(v, int))
        unobserved = [r["name"] for r in self.remotes if not r["observable"]]
        # A failed journald read (local rc!=0, or a probed remote erroring mid-window)
        # means underruns were NOT observed for that window — never score it clean.
        read_failed = (not local_ok) or any(v == "error" for v in remote_counts.values())
        valid = not stream_stalled and not read_failed
        clean = valid and local_n == 0 and observed_remote_total == 0 and total_flips == 0

        return {
            "config": config,
            "valid": valid,
            "clean": clean,
            "local_underruns": local_n,
            "remote_underruns": observed_remote_total,
            "remote_detail": remote_counts,
            "connection_flips": total_flips,
            "unobserved_remotes": unobserved,
            "stream_stalled": stream_stalled,
            "read_failed": read_failed,
            "sample_lines": local_lines[:3],
        }

    def _local_underruns(self, t0):
        rc, out, _ = journal_local(LOCAL_SNAPCLIENT_UNIT, t0, self.args.sudo)
        if rc != 0:
            return 0, [], False
        n, lines = count_underruns(out, self.pattern)
        return n, lines, True

    def _remote_underruns_total(self, t0):
        total = 0
        for r in self.remotes:
            if not r["observable"]:
                continue
            rc, out, _ = journal_remote(r["ip"], self.args.ssh_user, REMOTE_SNAPCLIENT_UNIT, t0,
                                        self.args.remote_sudo, self.args.ssh_askpass)
            if rc == 0:
                n, _ = count_underruns(out, self.pattern)
                total += n
        return total

    def _remote_underruns_detail(self, t0):
        detail = {}
        for r in self.remotes:
            if not r["observable"]:
                detail[r["name"]] = "unobserved"
                continue
            rc, out, _ = journal_remote(r["ip"], self.args.ssh_user, REMOTE_SNAPCLIENT_UNIT, t0,
                                        self.args.remote_sudo, self.args.ssh_askpass)
            detail[r["name"]] = count_underruns(out, self.pattern)[0] if rc == 0 else "error"
        return detail

    # -- run -----------------------------------------------------------------

    def run(self, grid):
        self.activate_radio()
        for i, config in enumerate(grid, 1):
            print(f"\n=== config {i}/{len(grid)}: {config} ===")
            try:
                self.apply_and_settle(config)
                row = self.monitor(config)
            except Exception as e:  # one bad config must not abort the whole sweep
                print(f"  ERROR measuring this config: {e}")
                row = {"config": config, "valid": False, "clean": False,
                       "local_underruns": 0, "remote_underruns": 0, "remote_detail": {},
                       "connection_flips": 0, "unobserved_remotes": [],
                       "stream_stalled": True, "read_failed": True, "sample_lines": []}
            self.results.append(row)
            self._print_row(row)
        self.api.radio_stop()
        self.report()

    def _print_row(self, row):
        if row["clean"]:
            verdict = "CLEAN"
        elif row["valid"]:
            verdict = "LOSS"
        else:
            verdict = "INVALID(read)" if row.get("read_failed") else "INVALID(stream)"
        print(f"  -> {verdict}: local={row['local_underruns']} "
              f"remotes={row['remote_underruns']} flips={row['connection_flips']} "
              f"detail={row['remote_detail']}")
        if row["sample_lines"]:
            print(f"     sample matched lines: {row['sample_lines']}")

    def report(self):
        print("\n" + "=" * 78)
        print("RESULTS (sorted by buffer_ms ascending; lowest CLEAN buffer = your optimum)")
        print("=" * 78)
        rows = sorted(self.results, key=lambda r: r["config"]["buffer_ms"])
        hdr = f"{'buffer':>7} {'codec':>5} {'chunk':>6} {'scbuf':>6} {'verdict':>16} {'loc':>4} {'rem':>4} {'flip':>4}"
        print(hdr)
        print("-" * len(hdr))
        for r in rows:
            c = r["config"]
            verdict = "CLEAN" if r["clean"] else ("INVALID" if not r["valid"] else "LOSS")
            print(f"{c['buffer_ms']:>7} {c['codec']:>5} {c['chunk_ms']:>6} "
                  f"{c['snapclient_buffer_time']:>6} {verdict:>16} "
                  f"{r['local_underruns']:>4} {r['remote_underruns']:>4} {r['connection_flips']:>4}")
        clean = [r for r in rows if r["clean"]]
        if clean:
            best = clean[0]
            print(f"\nLowest CLEAN config: {best['config']}")
            print("Confirm its absolute latency by hand (phone slow-mo: screen vs speaker) "
                  "and add ~30% buffer margin for everyday robustness.")
        else:
            print("\nNo CLEAN config in this grid. Raise the buffer range and re-run.")
        unobserved = sorted({n for r in rows for n in r["unobserved_remotes"]})
        if unobserved:
            print(f"\nWARNING: remotes never log-observed (connection-only, underruns NOT "
                  f"verified): {unobserved}\n         Set up SSH key access to verify them.")
        if self.args.csv:
            self._write_csv(rows)

    def _write_csv(self, rows):
        import csv
        with open(self.args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["buffer_ms", "codec", "chunk_ms", "snapclient_buffer_time",
                        "verdict", "local_underruns", "remote_underruns",
                        "connection_flips", "stream_stalled", "remote_detail"])
            for r in rows:
                c = r["config"]
                verdict = "CLEAN" if r["clean"] else ("INVALID" if not r["valid"] else "LOSS")
                w.writerow([c["buffer_ms"], c["codec"], c["chunk_ms"], c["snapclient_buffer_time"],
                            verdict, r["local_underruns"], r["remote_underruns"],
                            r["connection_flips"], r["stream_stalled"], json.dumps(r["remote_detail"])])
        print(f"CSV written to {self.args.csv}")


# ----------------------------------------------------------------------------- main

def load_grid(args):
    if args.grid:
        with open(args.grid) as f:
            grid = json.load(f)
        if not isinstance(grid, list):
            raise SystemExit("--grid file must contain a JSON array of config objects")
    else:
        grid = [dict(c) for c in DEFAULT_GRID]
    for c in grid:
        for k, v in c.items():
            if k not in VALIDATORS:
                raise SystemExit(f"Unknown config key '{k}' in grid")
            if not VALIDATORS[k](v):
                raise SystemExit(f"Invalid {k}={v} in grid (out of allowed range)")
        missing = [k for k in ("buffer_ms", "codec", "chunk_ms", "snapclient_buffer_time") if k not in c]
        if missing:
            raise SystemExit(f"Grid entry {c} missing required keys: {missing}")
    return grid


def main():
    ap = argparse.ArgumentParser(description="Snapcast latency/quality sweep")
    ap.add_argument("--base", default="http://localhost:8000", help="backend base URL")
    ap.add_argument("--rpc-host", default="localhost")
    ap.add_argument("--rpc-port", type=int, default=1780)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--station-id", help="play an existing radio favorite by id (most robust)")
    src.add_argument("--source-url", help="stream/file URL to play (file:// for a local loop-safe file)")
    ap.add_argument("--grid", help="JSON file: array of config dicts (overrides the default grid)")
    ap.add_argument("--window", type=int, default=600, help="measurement window per config, seconds")
    ap.add_argument("--poll", type=int, default=15, help="live poll interval, seconds")
    ap.add_argument("--stabilize", type=int, default=8,
                    help="seconds to wait after audio resumes before opening the measurement "
                    "window (skips the post-restart reconnect/ALSA-reopen transition)")
    ap.add_argument("--settle-timeout", type=int, default=60, help="max wait for clients to reconnect")
    ap.add_argument("--ssh-user", default="milo", help="ssh user for remote journald access")
    ap.add_argument("--ssh-askpass", help="path to an askpass helper (echoes the ssh password) "
                    "for transient password auth instead of key-based BatchMode")
    ap.add_argument("--sudo", action="store_true", default=True, help="use sudo for local journalctl")
    ap.add_argument("--no-sudo", dest="sudo", action="store_false")
    ap.add_argument("--remote-sudo", action="store_true", help="use 'sudo -n' for remote journalctl")
    ap.add_argument("--underrun-pattern", default=DEFAULT_UNDERRUN_PATTERN,
                    help="regex (IGNORECASE) flagged as audio loss")
    ap.add_argument("--csv", help="write results to this CSV path")
    ap.add_argument("--dry-run", action="store_true",
                    help="preflight only: check reachability, clients, SSH/journald; change nothing")
    args = ap.parse_args()

    if not args.dry_run and not (args.station_id or args.source_url):
        ap.error("a test source is required: pass --station-id or --source-url")

    api = Api(args.base)
    rpc = Rpc(args.rpc_host, args.rpc_port)
    sweep = Sweep(args, api, rpc)

    print("[preflight] reading current server config ...")
    original = sweep.read_original()
    print(f"  current: {original}")

    print("[preflight] discovering clients via Server.GetStatus ...")
    clients = sweep.discover()
    for c in clients:
        loc = " (local)" if c["ip"] in ("127.0.0.1", "::1", "") else ""
        print(f"  client {c['name'] or c['id']:<22} {c['ip']:<16} connected={c['connected']}{loc}")

    print("[preflight] checking journald observability ...")
    sweep.probe_local_observability()
    sweep.probe_remote_observability()

    grid = load_grid(args)
    est = len(grid) * (args.window + 20) / 60.0
    print(f"\n[plan] {len(grid)} configs x ~{args.window}s window "
          f"=> ~{est:.0f} min total (plus restarts)")
    for c in grid:
        print(f"  - {c}")

    if args.dry_run:
        print("\n[dry-run] no changes made. Re-run without --dry-run to start the sweep.")
        return

    # Restore original config on any exit path, including Ctrl-C.
    def _sigint(_sig, _frm):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _sigint)
    try:
        sweep.run(grid)
    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        with contextlib.suppress(Exception):
            api.radio_stop()
        sweep.restore()


if __name__ == "__main__":
    main()
