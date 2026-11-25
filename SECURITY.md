# Security policy

## Reporting a vulnerability

Report it privately, through GitHub's private vulnerability reporting: the **Security** tab of
this repository, then **Report a vulnerability**
([direct link](https://github.com/leodurandfr/Milo/security/advisories/new)). Do not open a
public issue, pull request or discussion for it.

A useful report says:

- the Milō version (Settings › System) and whether the unit is a main unit or a multiroom
  speaker;
- the Raspberry Pi model and the audio card;
- what an attacker needs: where they sit (on the home network, on the recovery access point,
  on the internet) and what they must already know;
- the steps to reproduce, and what they lead to.

A diagnostic report (Settings › System › Diagnostic) can help. Read it before you attach it.

The answer comes in the advisory's own thread. A confirmed vulnerability is fixed in a new
release, and the advisory is published once that release is out, crediting you unless you ask
otherwise.

## Supported versions

Only the **latest published release** is supported. A fix ships as a new release, which every
unit is offered under Settings › Updates; nothing is backported to an older one.

- A pre-release (a tag with a suffix, such as `v0.2.0-rc1`) is never offered to units and is
  not supported.
- A development build (a checkout that does not sit exactly on a release tag) is offered no
  update and is not supported.

A unit installs a release only once its manifest verifies against the signing key the running
release carries.

The programs Milō installs behind its sources (shairport-sync, go-librespot, snapcast,
CamillaDSP and the others in `dependencies.env`) and the Debian packages of the OS have their
own upstreams: report a flaw in one of them there. Report it here when it lies in how Milō
installs, configures or exposes that program. Debian's security fixes install on every unit by
themselves (`unattended-upgrades`, the `Debian-Security` origin only).

## Threat model

Milō is built for a **trusted home network**. Whoever can reach a unit on the local network
controls it, root included. **Do not expose a unit to the internet**: no port forwarding, no
DMZ.

### What is not protected

- **Nothing on the API authenticates.** The REST and WebSocket API answers whoever reaches it,
  on port 80 (nginx) and on port 8000 (the backend, on every interface). That includes the
  routes that amount to root: opening SSH, setting the `milo` password, the same two relayed to
  a multiroom speaker, installing a program version past the validated set, and resetting the
  setup. The backend's sudo grants make its `milo` user root by construction.
- **There is no proof of presence.** None of those routes waits for a button press or a
  confirmation on the unit itself.
- **No TLS, no rate limiting.** The web interface and the API are plain HTTP on the local
  network, and every request is answered.
- **A multiroom speaker's API (port 8001)** is open to the local network for reading and for
  its settings, and for its two dependency installs (`POST /update`, `POST /camilladsp/update`),
  which install as root a release upstream published, at the version the caller names.

### What stands

- **Request origin gate.** The backend refuses (403) a request whose `Host` is not one of the
  unit's names or a private address, which defeats DNS rebinding, and a state-changing request
  whose `Origin` is a foreign site. A web page on the internet cannot drive a browser on the
  local network into sending commands, or read the WebSocket. A page served by another machine
  on the local network passes. Each speaker's API has a stricter twin.
- **CORS** lets only `milo.local` and the development server read a cross-origin response.
- **SSH** is off on a fresh image, and opening it is refused while the factory password is in
  place: that password is the same on every unit and published with the source.
- **The recovery access point**, an open WiFi network a unit raises when it has no link, admits
  reads only, plus the setup wizard's few writes before setup and saving a WiFi network after it
  (`RecoveryAccessPointGuard`). sshd refuses every connection from its subnet, `10.42.0.0/24`.
- **A multiroom speaker's paths to root** (its app update, SSH switch, password and reboot)
  answer only the server whose secret it pinned. A speaker pins the first server that reaches
  it with one, and a speaker that answers another server is named in Settings › Multiroom.
  After a boot with no clean stop before it (a power cut, which is also how a speaker is moved
  to a new server), it accepts a new server's secret for five minutes: the first one presented
  wins, and the speaker announces that window in its heartbeat.
- **Unit hardening.** Every daemon running as `milo` other than the backend has
  `NoNewPrivileges=true`, so it cannot use the backend's sudo grants. The source daemons also
  have `ProtectHome=read-only`, so none can rewrite the checkout systemd runs as the backend.
- **IPv6 firewall**, on main units and speakers alike: a new inbound IPv6 connection is
  accepted only from the link (`fe80::/10`), a unique local address (`fc00::/7`) or a global
  prefix the unit's own interfaces carry. IPv4 is not filtered: it relies on the router's NAT.
- **snapserver's control** (JSON-RPC, which authenticates nothing) listens on loopback for the
  backend alone, and its TCP control port is off.

### Known limits

- **snapserver's stream port (1704) is open to the local network**, with no authentication.
  Any device there can connect as a snapcast client, receive the multiroom stream and be
  registered as a speaker; one that connects under an existing speaker's identity is handed
  that speaker's secret.
- **A speaker can be taken over after a power cut.** Any host on the local network that reaches
  a speaker with a secret within five minutes of a boot with no clean stop before it, ahead of
  the speaker's own server, becomes the server it answers, root included.
- **The home WiFi key crosses open links.** During setup over the recovery access point, and
  during the WiFi adoption of a new speaker over its open `Milō` hotspot, it travels in plain HTTP
  over an open network to a unit that is not authenticated, so a fake `Milō` network in range
  can collect it. Ethernet avoids both. It is also pushed to every speaker as a standby link,
  unauthenticated, over the local network.
- **The recovery access point can move a unit onto another network.** The access point is
  open, so while a set-up unit has no link, anyone in radio range can join it and save a WiFi
  network of their choice. The unit tries that network about five seconds later and joins it if
  it can. It is then on the network that person chose, where the API answers them without
  authentication, root included. A unit whose Ethernet link holds raises no access point.
- **mDNS is not authenticated.** Any host on the local network can answer for `milo.local`,
  the name browsers and the apps dial, or announce `_milo._tcp`, which a freshly flashed card
  looks for before it turns itself into a multiroom speaker.
- **The IPv6 firewall follows renumbering late.** A router that changes the global prefix by
  router advertisement alone reaches the firewall's allowed set only at the next
  NetworkManager event on the unit.
- **Qobuz.** While Qobuz is the selected source, qobuz-proxy's Connect endpoint (port 8690) is
  open to the local network with no gate. Its web UI (port 8689) is gated, but upstream's OAuth
  callback carries no `state`, so a login CSRF cannot be told from a real login.
- **Same user, same reach.** The speaker key the server derives every secret from is readable
  by every process running as `milo`; such a process can also ptrace the backend, and the
  daemons without `ProtectHome` (kiosk, snapserver, snapclient, CamillaDSP, Navidrome) can write
  the checkout the backend runs.

The full detail, with the file that implements each point, is in
[docs/architecture.md § Security](docs/architecture.md#security).
