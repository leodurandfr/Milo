#!/bin/bash -e
# Milo pi-gen stage: Deploy rootfs files, systemd services, and configurations

# ── Data directories ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /var/lib/milo
chown -R milo:milo /var/lib/milo
CHROOT

# ── Systemd services ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
for service_file in /home/milo/milo/system/*.service; do
    if [ -f "$service_file" ]; then
        cp "$service_file" /etc/systemd/system/
    fi
done

# Avahi override
mkdir -p /etc/systemd/system/avahi-daemon.service.d
cp /home/milo/milo/system/avahi-daemon-override.conf \
    /etc/systemd/system/avahi-daemon.service.d/milo-override.conf

# milo-client services
for service_file in /home/milo/milo/milo-client/system/*.service; do
    if [ -f "$service_file" ]; then
        cp "$service_file" /etc/systemd/system/
    fi
done

# milo-client Avahi override
if [ -f /home/milo/milo/milo-client/system/avahi-daemon-override.conf ]; then
    cp /home/milo/milo/milo-client/system/avahi-daemon-override.conf \
        /etc/systemd/system/avahi-daemon.service.d/milo-client-override.conf
fi

systemctl daemon-reload
CHROOT

# ── ALSA configuration ───────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# ALSA loopback: 2 cards — "Loopback" (DSP slot 0 + 7 sources) and "LoopbackDLNA"
# (DLNA + future sources). snd-aloop caps at 8 substreams/card, so an 8th source
# needs a second card rather than a 9th substream (which the driver rejects).
echo "snd-aloop" > /etc/modules-load.d/snd-aloop.conf
echo "options snd-aloop index=1,2 enable=1,1 id=Loopback,LoopbackDLNA pcm_substreams=8,8" > /etc/modprobe.d/snd-aloop.conf

# ALSA routing + env files (asound.conf, routing.env, snapclient.env, mac.env).
# Reuse install/alsa.sh::configure_alsa_complete so pi-gen and the bash installer
# write identical files — single source of truth. Inline-writing only routing.env
# (the old behaviour) left snapclient.env and mac.env missing on the image.
cd /home/milo/milo
source install/common.sh
source install/alsa.sh
configure_alsa_complete
CHROOT

# ── CamillaDSP configuration ─────────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /var/lib/milo/camilladsp/configs /var/lib/milo/camilladsp/coeffs
cp /home/milo/milo/rootfs/var/lib/milo/camilladsp/config.yml /var/lib/milo/camilladsp/config.yml
chown -R milo:milo /var/lib/milo/camilladsp
CHROOT

# ── go-librespot configuration ───────────────────────────────────────────────
# Reuse install/go-librespot.sh::configure_go_librespot so pi-gen and the bash
# installer write an identical config.yml — single source of truth. Inline-writing
# it here is what shipped the image without `zeroconf_backend: avahi`, letting
# go-librespot's embedded mDNS responder broadcast milo._spotify-connect on wlan0
# and race Avahi into the milo.local → milo-2.local rename (HostnameConflict popup).

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/go-librespot.sh
configure_go_librespot
CHROOT

# ── qobuz-proxy (Qobuz Connect) ──────────────────────────────────────────────
# Reuse install/qobuz-proxy.sh::install_qobuz_proxy so pi-gen and the bash
# installer build an identical venv + config.yaml — single source of truth.
# Installs libportaudio2 + the pinned git tag into /var/lib/milo/qobuz/venv and
# writes the flat single-speaker config. The one-time Qobuz login is done by the
# operator later (settings screen / :8689) and cached in credentials.json — it
# is user-specific and intentionally NOT baked into the image.

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/qobuz-proxy.sh
install_qobuz_proxy
CHROOT

# ── Navidrome (Music Library catalog engine) ─────────────────────────────────
# The binary is downloaded in the audio stage (01-install-audio); here we only
# write its config + prepare dirs, reusing install/navidrome.sh::configure_navidrome
# so pi-gen and the bash installer emit an identical navidrome.toml — single source
# of truth (same pattern as go-librespot). The per-device service-account password
# is NOT baked: milo-navidrome-provision generates it on first boot.

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/navidrome.sh
configure_navidrome
CHROOT

# ── Snapserver configuration ─────────────────────────────────────────────────
# Reuse install/snapcast.sh::configure_snapserver to keep a single source of truth
# for /etc/snapserver.conf — pi-gen and bash install.sh write the same content.

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/snapcast.sh
configure_snapserver
CHROOT

# ── shairport-sync configuration ─────────────────────────────────────────────

on_chroot << 'CHROOT'
tee /etc/shairport-sync.conf > /dev/null << 'CONF'
// Milo AirPlay 2 Configuration
general = {
    name = "Milō · AirPlay";
    interpolation = "auto";
    output_backend = "alsa";
    mdns_backend = "avahi";
    ignore_volume_control = "yes";
};

alsa = {
    output_device = "milo_airplay";
};

metadata = {
    enabled = "yes";
    include_cover_art = "yes";
    pipe_name = "/tmp/shairport-sync-metadata";
    pipe_timeout = 5000;
};
CONF

# D-Bus policy for shairport-sync
tee /etc/dbus-1/system.d/shairport-sync-dbus.conf > /dev/null << 'DBUS'
<!-- D-Bus policy for shairport-sync (Milo AirPlay) -->
<!DOCTYPE busconfig PUBLIC
          "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
          "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="org.gnome.ShairportSync"/>
  </policy>
  <policy user="shairport-sync">
    <allow own="org.gnome.ShairportSync"/>
  </policy>
  <policy user="milo">
    <allow own="org.gnome.ShairportSync"/>
  </policy>
  <policy context="default">
    <allow send_destination="org.gnome.ShairportSync"/>
    <allow receive_sender="org.gnome.ShairportSync"/>
  </policy>
</busconfig>
DBUS
CHROOT

# ── Nginx configuration ──────────────────────────────────────────────────────

on_chroot << 'CHROOT'
tee /etc/nginx/sites-available/milo > /dev/null << 'NGINX'
upstream milo_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name milo.local localhost _;

    client_max_body_size 10M;

    root /home/milo/milo/frontend/dist;
    index index.html;

    location ^~ /api/radio/images/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    location /api/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    location /ws {
        proxy_pass http://milo_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }
}
NGINX

ln -sf /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/milo
rm -f /etc/nginx/sites-enabled/default
CHROOT

# ── Nginx permissions ────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
chmod 755 /home/milo
chmod 755 /home/milo/milo
chmod 755 /home/milo/milo/frontend
chmod -R 755 /home/milo/milo/frontend/dist
chown -R milo:milo /home/milo/milo/frontend/dist
CHROOT

# ── Avahi configuration ──────────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/avahi/avahi-daemon.conf /etc/avahi/avahi-daemon.conf

tee /etc/avahi/services/milo.service > /dev/null << 'XML'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Milo Audio System on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
  </service>
  <service>
    <type>_snapcast._tcp</type>
    <port>1705</port>
  </service>
</service-group>
XML
CHROOT

# ── NetworkManager dispatcher ────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/NetworkManager/dispatcher.d/90-milo-network /etc/NetworkManager/dispatcher.d/
chmod 755 /etc/NetworkManager/dispatcher.d/90-milo-network

# Captive portal DNS redirect for hotspot mode
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cp /home/milo/milo/rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf /etc/NetworkManager/dnsmasq-shared.d/

# Disable WiFi power saving (replaces the old one-shot iw unit)
mkdir -p /etc/NetworkManager/conf.d
cp /home/milo/milo/rootfs/etc/NetworkManager/conf.d/90-milo-wifi-powersave.conf /etc/NetworkManager/conf.d/

# NetworkManager connectivity check — drop-in read by the backend connectivity
# D-Bus subscriber (backend/core/connectivity/service.py). Written inline because
# install/network.sh::configure_nm_connectivity also reloads/restarts NetworkManager,
# which is not valid inside the build chroot.
mkdir -p /etc/NetworkManager/conf.d
tee /etc/NetworkManager/conf.d/99-milo-connectivity.conf > /dev/null << 'EOF'
[connectivity]
uri=http://nmcheck.gnome.org/check_network_status.txt
interval=300
EOF
CHROOT

# ── PolicyKit rules ───────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /etc/polkit-1/rules.d
cp /home/milo/milo/rootfs/etc/polkit-1/rules.d/50-milo-networkmanager.rules \
    /etc/polkit-1/rules.d/50-milo-networkmanager.rules
chmod 0644 /etc/polkit-1/rules.d/50-milo-networkmanager.rules
CHROOT

# ── Bluetooth device name ────────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/machine-info /etc/machine-info
CHROOT

# ── Scripts and tools ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Shared hardware helpers library (used by both server and client apply scripts)
mkdir -p /usr/local/lib/milo
cp /home/milo/milo/rootfs/usr/local/lib/milo/hardware-helpers.sh /usr/local/lib/milo/
chmod +x /usr/local/lib/milo/hardware-helpers.sh

# Deploy ALL server scripts from rootfs/usr/local/bin in one loop. A hand-maintained
# per-file allowlist silently drops newly-added scripts — that is exactly how
# milo-apply-avahi-iface, milo-apply-ir-keymap and milo-ir-keytable-setup went
# missing from the image (avahi-daemon then failed 203/EXEC on the missing helper).
for script in /home/milo/milo/rootfs/usr/local/bin/*; do
    if [ -f "$script" ]; then
        cp "$script" /usr/local/bin/
        chmod +x "/usr/local/bin/$(basename "$script")"
    fi
done

# milo-client scripts (universal image supports both server and client roles)
if [ -d /home/milo/milo/milo-client/rootfs/usr/local/bin ]; then
    for script in /home/milo/milo/milo-client/rootfs/usr/local/bin/*; do
        if [ -f "$script" ]; then
            cp "$script" /usr/local/bin/
            chmod +x "/usr/local/bin/$(basename "$script")"
        fi
    done
fi
CHROOT

# ── Hardware configuration ───────────────────────────────────────────────────
# Intentionally NOT seeded. /var/lib/milo/hardware.json is created by the backend
# (save_versioned_json, which always stamps the current schema_version) when the
# user picks hardware in the setup wizard. A bash-seeded file cannot be kept in
# sync with the schema: it shipped a stale, unversioned file that crash-looped the
# backend with SchemaVersionMismatch. Absent file → backend uses its in-code
# defaults (see backend/hardware/registry.py).
