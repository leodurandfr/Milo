# Milo Client — the satellite environment file

`/var/lib/milo-client/env` is the `EnvironmentFile=` of two satellite units,
`milo-client` and `milo-client-snapclient` (`milo-client-camilladsp` reads none:
its configuration is `hardware.json` and its own `config.yml`). It is **written
by `milo-first-boot`** when a flashed card detects a Milō server on the network
and converts itself into a satellite — there is no installer to run — and holds
one line then:

```bash
MILO_PRINCIPAL_IP=milo.local
```

The server adds the snapclient keys below the first time it pushes them.

## The keys

### `MILO_PRINCIPAL_IP` — where the server is

`milo-first-boot` writes the string `milo.local`, on purpose: the satellite then
survives the server moving between Ethernet and WiFi, which a baked IP would not.
A unit with no entry at all uses `milo.local` too.

The satellite looks for its server in this order — the app at every heartbeat
and every address check (`services/registration.py::resolve_milo_principal`),
`milo-client-snapclient-launcher` at every start of snapclient:

1. **`MILO_PRINCIPAL_IP`, when it is a literal IPv4 address.** Nothing else is
   asked, and the address watcher never moves the stream off it: an address
   written here by hand is a pin.
2. **That name, resolved** — `milo.local` over mDNS, unless the file names
   another host.
3. **The server's last known addresses**, in
   `/var/lib/milo-client/server-addresses`: every IPv4 address the server
   listed in its last heartbeat answer, wired first, one per line. The app
   rewrites the file whenever the list changes, atomically. The first that
   accepts a connect on the server's port (8000) is used.

The third step is the way back on a LAN whose router filters multicast, a mesh
router's habit: there `milo.local` never resolves, and a speaker that restarts
would wait for a heartbeat that cannot reach the server. A speaker that has
spoken to its server once keeps finding it, as long as the server keeps one of
the addresses it listed — so on such a LAN, give the server a DHCP reservation.
A card that never reached its server knows no address yet: on that LAN only a
literal `MILO_PRINCIPAL_IP` gets it there.

When the watcher restarts snapclient to follow the server, it hands the
launcher the address it found (`/run/milo-client/server-address`, read once and
removed), so the restart does not look again.

### `MILO_SNAPCLIENT_BUFFER_TIME` / `MILO_SNAPCLIENT_FRAGMENTS` — the server's

The **server owns these**. It pushes them over `PUT /snapclient/config` on the
satellite's API (port 8001), which rewrites this file and restarts snapclient
only when a value actually changed. A value written here by hand is replaced at
the next push.

They may be absent, in which case `milo-client-snapclient-launcher` applies its
own defaults — `buffer_time=80`, `fragments=4`, chosen for WiFi. Lower means
lower latency and less tolerance for network jitter: ~20 ms is right on wired
Ethernet, 40–80 ms on WiFi.

## Verifying

```bash
# What snapclient is actually running with
ps aux | grep snapclient          # → --player alsa:buffer_time=…,fragments=…
sudo journalctl -u milo-client-snapclient -f
#   (Alsa) Using configured buffer_time: 20 ms, configured fragments: 4

# Is the server reachable
ping milo.local
cat /var/lib/milo-client/server-addresses   # the server's last known addresses
sudo journalctl -u milo-client -f
```

The satellite has no log surface in the UI: these are read over SSH, which a
satellite ships with off and the server opens from the satellite's panel under
Settings › Multiroom.
