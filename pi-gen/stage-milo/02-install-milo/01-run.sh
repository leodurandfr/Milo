#!/bin/bash -e
# Milo pi-gen stage: Deploy rootfs files, systemd services, and configurations

MILO_APP_DIR="/home/milo/milo"
MILO_DATA_DIR="/var/lib/milo"

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
# ALSA loopback module
echo "snd-aloop" > /etc/modules-load.d/snd-aloop.conf
echo "options snd-aloop index=1 enable=1 pcm_substreams=8" > /etc/modprobe.d/snd-aloop.conf

# ALSA device routing
cp /home/milo/milo/rootfs/etc/asound.conf /etc/asound.conf

# Default routing environment
tee /var/lib/milo/routing.env > /dev/null << 'EOF'
MILO_MODE=direct
EOF
chown milo:milo /var/lib/milo/routing.env
CHROOT

# ── CamillaDSP configuration ─────────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /var/lib/milo/camilladsp/configs /var/lib/milo/camilladsp/coeffs
cp /home/milo/milo/rootfs/var/lib/milo/camilladsp/config.yml /var/lib/milo/camilladsp/config.yml
chown -R milo:milo /var/lib/milo/camilladsp
CHROOT

# ── go-librespot configuration ───────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /var/lib/milo/go-librespot
tee /var/lib/milo/go-librespot/config.yml > /dev/null << 'EOF'
device_name: "Milō"
device_type: "speaker"
bitrate: 320

audio_backend: "alsa"
audio_device: "milo_spotify"

external_volume: true

server:
  enabled: true
  address: localhost
  port: 3678
  allow_origin: "*"
  image_size: 'xlarge'
EOF
chown -R milo:audio /var/lib/milo/go-librespot
CHROOT

# ── Snapserver configuration ─────────────────────────────────────────────────

on_chroot << 'CHROOT'
tee /etc/snapserver.conf > /dev/null << 'EOF'

[stream]
default_source = Multiroom

buffer = 250
codec = flac
chunk_ms = 15
sampleformat = 48000:32:2

source = meta:///Bluetooth/ROC/Spotify/Radio/Podcast/AirPlay?name=Multiroom

source = alsa:///?name=Bluetooth&device=hw:1,1,0&idle_threshold=5000&send_silence=true
source = alsa:///?name=ROC&device=hw:1,1,1&idle_threshold=5000&send_silence=true
source = alsa:///?name=Spotify&device=hw:1,1,2&idle_threshold=5000&send_silence=true
source = alsa:///?name=Radio&device=hw:1,1,3&idle_threshold=5000&send_silence=true
source = alsa:///?name=Podcast&device=hw:1,1,4&idle_threshold=5000&send_silence=true
source = alsa:///?name=AirPlay&device=hw:1,1,6&idle_threshold=5000&send_silence=true

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
doc_root = /usr/share/snapserver/snapweb/

[server]
threads = 4

[logging]
enabled = true
EOF
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
CHROOT

# ── Bluetooth device name ────────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/machine-info /etc/machine-info
CHROOT

# ── Scripts and tools ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Readiness script
cp /home/milo/milo/rootfs/usr/local/bin/milo-wait-ready.sh /usr/local/bin/
chmod +x /usr/local/bin/milo-wait-ready.sh

# Hardware apply script
cp /home/milo/milo/rootfs/usr/local/bin/milo-apply-hardware /usr/local/bin/
chmod +x /usr/local/bin/milo-apply-hardware

# Brightness control for Waveshare 7" USB
cp /home/milo/milo/rootfs/usr/local/bin/milo-brightness-7 /usr/local/bin/
chmod +x /usr/local/bin/milo-brightness-7

# milo-client scripts
if [ -d /home/milo/milo/milo-client/rootfs/usr/local/bin ]; then
    for script in /home/milo/milo/milo-client/rootfs/usr/local/bin/*; do
        if [ -f "$script" ]; then
            cp "$script" /usr/local/bin/
            chmod +x "/usr/local/bin/$(basename "$script")"
        fi
    done
fi
CHROOT

# ── Default hardware configuration ───────────────────────────────────────────

on_chroot << 'CHROOT'
tee /var/lib/milo/hardware.json > /dev/null << 'EOF'
{
  "screen": {
    "type": "none",
    "resolution": null
  },
  "audio": {
    "id": "none"
  },
  "rotary_encoder": {
    "clk_pin": 22,
    "dt_pin": 27,
    "sw_pin": 23
  }
}
EOF
chown milo:milo /var/lib/milo/hardware.json
CHROOT
