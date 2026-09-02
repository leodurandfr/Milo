# Milo Client — the satellite environment file

`/var/lib/milo-client/env` is the `EnvironmentFile=` of the three satellite units
(`milo-client`, `milo-client-snapclient`, `milo-client-camilladsp`). It is
**written by `milo-first-boot`** when a flashed card detects a Milō server on the
network and converts itself into a satellite — there is no installer to run.

```bash
MILO_PRINCIPAL_IP=milo.local
MILO_CLIENT_DSP_ENABLED=false
```

## The keys

### `MILO_PRINCIPAL_IP` — where the server is

`milo-first-boot` writes the string `milo.local`, on purpose: the satellite then
survives the server moving between Ethernet and WiFi, which a baked IP would not.
`milo-client-snapclient-launcher` resolves it at every service start, and
`services/registration.py` accepts either form.

**Replace it with a literal IPv4 address on a LAN where mDNS does not work** —
that is the one edit an operator makes here by hand:

```bash
sudo sed -i 's/^MILO_PRINCIPAL_IP=.*/MILO_PRINCIPAL_IP=192.168.1.73/' /var/lib/milo-client/env
sudo systemctl restart milo-client-snapclient milo-client
```

A unit with no entry at all falls back to mDNS.

### `MILO_SNAPCLIENT_BUFFER_TIME` / `MILO_SNAPCLIENT_FRAGMENTS` — do not edit

The **server owns these**. It pushes them over `PUT /snapclient/config` on the
satellite's API (port 8001), which rewrites this file and restarts snapclient
only when a value actually changed. Hand-editing them works until the next push
and then silently reverts.

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
sudo journalctl -u milo-client -f
```

The satellite has no log surface in the UI. From the Milō server, `sat logs`,
`sat audio` and `sat sync` read all of this over SSH.
