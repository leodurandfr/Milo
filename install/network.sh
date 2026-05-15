#!/bin/bash
# Milo - Network Configuration (Avahi + Nginx + Chromium)
#
# Installs and configures Avahi (mDNS), Nginx (reverse proxy),
# Chromium (kiosk browser), and network dispatcher.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_avahi_nginx() {
    log_info "Installing Avahi, Nginx and Chromium..."

    sudo apt install -y avahi-daemon avahi-utils nginx

    # Install Chromium (handles both package names)
    if ! sudo apt install -y chromium 2>/dev/null; then
        log_info "Trying with chromium-browser..."
        sudo apt install -y chromium-browser
    fi

    log_success "Avahi, Nginx and Chromium installed"
}

configure_avahi() {
    log_info "Configuring Avahi (mDNS)..."

    # Copy Avahi config (eth0 allowed, wlan0 denied by default)
    log_info "Installing Avahi config (eth0 default)..."
    sudo cp "$MILO_APP_DIR/rootfs/etc/avahi/avahi-daemon.conf" /etc/avahi/avahi-daemon.conf

    # Install the helper that rewrites allow-/deny-interfaces from the state
    # file before every Avahi start (see milo-apply-avahi-iface for the why).
    log_info "Installing Avahi interface-apply helper..."
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-apply-avahi-iface" /usr/local/bin/milo-apply-avahi-iface
    sudo chmod +x /usr/local/bin/milo-apply-avahi-iface

    # Install systemd override that calls the helper at every Avahi start.
    log_info "Installing Avahi boot reset override..."
    sudo mkdir -p /etc/systemd/system/avahi-daemon.service.d
    sudo cp "$MILO_APP_DIR/system/avahi-daemon-override.conf" \
        /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo systemctl daemon-reload

    sudo systemctl enable avahi-daemon
    sudo systemctl start avahi-daemon

    sudo tee /etc/avahi/services/milo.service > /dev/null << 'EOF'
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
EOF

    sudo systemctl restart avahi-daemon

    # Install NetworkManager dispatcher (Avahi mDNS interface selection)
    log_info "Installing network dispatcher..."
    sudo cp "$MILO_APP_DIR/rootfs/etc/NetworkManager/dispatcher.d/90-milo-network" /etc/NetworkManager/dispatcher.d/
    sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-milo-network

    # Remove legacy dispatchers from older installations
    sudo rm -f /etc/NetworkManager/dispatcher.d/98-wifi-eth0-priority
    sudo rm -f /etc/NetworkManager/dispatcher.d/99-avahi-interface

    # Install dnsmasq config for captive portal DNS redirect (hotspot mode)
    sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
    sudo cp "$MILO_APP_DIR/rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/

    log_success "Avahi configured (access via milo.local)"
}

configure_nginx() {
    log_info "Configuring Nginx..."

    sudo tee /etc/nginx/sites-available/milo > /dev/null << 'EOF'
upstream milo_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name milo.local localhost _;

    # Allow file uploads up to 10MB (images are limited to 5MB by the backend)
    client_max_body_size 10M;

    # Serve frontend static files directly from /dist
    root /home/milo/milo/frontend/dist;
    index index.html;

    # Radio images - must come BEFORE static files regex
    location ^~ /api/radio/images/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    # Cache static assets for better performance
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # Backend API endpoints
    location /api/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffering for real-time API responses
        proxy_buffering off;
    }

    # WebSocket endpoint for real-time updates
    location /ws {
        proxy_pass http://milo_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # Long timeout for WebSocket connections
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    # Serve index.html for all other routes (SPA routing)
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }
}
EOF

    sudo ln -sf /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/milo
    sudo rm -f /etc/nginx/sites-enabled/default

    sudo nginx -t
    sudo systemctl reload nginx

    log_success "Nginx configured to serve frontend directly from /dist"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_avahi_nginx
    configure_avahi
    configure_nginx
    log_success "Network configuration complete"
fi
