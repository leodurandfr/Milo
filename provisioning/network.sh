#!/bin/bash
# Milo - Nginx site configuration
#
# Writes /etc/nginx/sites-available/milo and enables it. Avahi, the
# NetworkManager drop-ins and the package installs are done by
# pi-gen/stage-milo directly.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"

# No service interaction on purpose: `nginx -t` and `systemctl reload nginx` are
# not valid inside the build chroot, so the reload is left to first boot.
write_nginx_site() {
    tee /etc/nginx/sites-available/milo > /dev/null << 'EOF'
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

    ln -sf /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/milo
    rm -f /etc/nginx/sites-enabled/default
}

