
# 06 · Create systemd.service files 



## Backend

**milo-backend.service** :

```bash
sudo tee /etc/systemd/system/milo-backend.service > /dev/null << 'EOF'
[Unit]
Description=Milo Backend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/Milo
ExecStart=/home/milo/Milo/venv/bin/python3 backend/main.py

Restart=always
RestartSec=5

# Timeout normal car systemd gère les plugins automatiquement
TimeoutStopSec=10

# Répertoire d'état
StateDirectory=milo
StateDirectoryMode=0755

# Logs
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

## Frontend

**milo-frontend.service**

```bash
sudo tee /etc/systemd/system/milo-frontend.service > /dev/null << 'EOF'
[Unit]
Description=Milo Frontend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/Milo/frontend

# Build et serve en production
ExecStartPre=/usr/bin/npm run build
ExecStart=/usr/bin/npm run preview -- --host 0.0.0.0 --port 3000

Restart=always
RestartSec=5
TimeoutStopSec=10

# Répertoire d'état
StateDirectory=milo
StateDirectoryMode=0755

# Variables d'environnement
Environment=NODE_ENV=production
Environment=HOME=/home/milo

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-frontend

[Install]
WantedBy=multi-user.target
EOF
```


## Kiosk mode

```bash
sudo tee /etc/systemd/system/milo-kiosk.service > /dev/null << 'EOF'
[Unit]
Description=Milo Kiosk Mode (Chromium Fullscreen)
After=graphical.target milo-frontend.service
Wants=graphical.target
Requires=milo-frontend.service

[Service]
Type=simple
User=milo
Group=milo
Environment=DISPLAY=:0
Environment=HOME=/home/milo
Environment=XDG_RUNTIME_DIR=/run/user/1000

# Attendre que le frontend soit prêt
ExecStartPre=/bin/sleep 8

# Lancer Chromium en mode kiosque tactile
ExecStart=/usr/bin/chromium-browser \
  --kiosk \
  --incognito \
  --no-first-run \
  --disable-infobars \
  --disable-notifications \
  --disable-popup-blocking \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-translate \
  --disable-sync \
  --hide-scrollbars \
  --disable-background-networking \
  --autoplay-policy=no-user-gesture-required \
  --start-fullscreen \
  --no-sandbox \
  --disable-dev-shm-usage \
  --hide-cursor \
  --touch-events=enabled \
  --enable-features=TouchpadAndWheelScrollLatching \
  --force-device-scale-factor=1 \
  --disable-pinch \
  --disable-features=VizDisplayCompositor \
  --app=http://milo.local


Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=graphical.target
EOF
```



## ROC

**milo-roc.service** 

```bash
sudo tee /etc/systemd/system/milo-roc.service > /dev/null << 'EOF'
[Unit]
Description=Milo ROC Audio Receiver
Documentation=https://roc-streaming.org/
After=network.target sound.service milo-backend.service
Wants=network.target
BindsTo=milo-backend.service

[Service]
Type=exec
User=milo
Group=audio

EnvironmentFile=/etc/environment
Environment=HOME=/home/milo

ExecStart=/usr/bin/roc-recv -vv \
  -s rtp+rs8m://0.0.0.0:10001 \
  -r rs8m://0.0.0.0:10002 \
  -c rtcp://0.0.0.0:10003 \
  -o alsa://milo_roc

Restart=always
RestartSec=5

PrivateNetwork=false
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-roc

[Install]
WantedBy=multi-user.target
EOF
```


## go-librespot

**milo-go-librespot.service** 
```bash
sudo tee /etc/systemd/system/milo-go-librespot.service > /dev/null << 'EOF'
[Unit]
Description=Milo Spotify Connect via go-librespot
After=network-online.target sound.service milo-backend.service
Wants=network-online.target
BindsTo=milo-backend.service

[Service]
Type=simple
User=milo
Group=audio

ExecStart=/usr/local/bin/go-librespot --config_dir /var/lib/milo/go-librespot
Environment=HOME=/home/milo
WorkingDirectory=/var/lib/milo
Restart=always
RestartSec=5

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-go-librespot

[Install]
WantedBy=multi-user.target
EOF
```


## Bluealsa :

**milo-bluealsa-aplay.service**
```bash
sudo tee /etc/systemd/system/milo-bluealsa-aplay.service > /dev/null << 'EOF'
[Unit]
Description=BlueALSA player for Milo
Requires=milo-bluealsa.service
After=milo-bluealsa.service milo-backend.service
BindsTo=milo-backend.service milo-bluealsa.service

[Service]
Type=simple
User=milo

ExecStart=/usr/bin/bluealsa-aplay --pcm=milo_bluetooth --profile-a2dp 00:00:00:00:00:00

RestartSec=2
Restart=always
PrivateTmp=false
PrivateDevices=false

[Install]
WantedBy=multi-user.target
EOF
```

**milo-bluealsa.service**
```bash
sudo tee /etc/systemd/system/milo-bluealsa.service > /dev/null << 'EOF'
[Unit]
Description=BluezALSA daemon for Milo
Documentation=man:bluealsa(8)
After=dbus.service bluetooth.service milo-backend.service
Requires=dbus.service
Wants=bluetooth.service
BindsTo=milo-backend.service

[Service]
Type=simple
ExecStart=/usr/bin/bluealsa -S -p a2dp-sink
User=milo
Group=audio
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```


## Snapcast

**milo-snapserver-multiroom.service**

```bash
sudo tee /etc/systemd/system/milo-snapserver-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Server for Milo Multiroom
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapserver -c /etc/snapserver.conf
User=milo
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**milo-snapclient-multiroom.service**

```bash
sudo tee /etc/systemd/system/milo-snapclient-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Client for Milo Multiroom
After=network-online.target milo-snapserver-multiroom.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapclient -h 127.0.0.1 -p 1704 --logsink=system --soundcard default:CARD=sndrpihifiberry --mixer hardware:'Digital'
User=milo
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**Démarrage automatique**

```bash
sudo systemctl daemon-reload
sudo systemctl enable milo-backend.service
sudo systemctl enable milo-frontend.service
sudo systemctl enable milo-kiosk.service
sudo systemctl enable milo-snapclient-multiroom.service
sudo systemctl enable milo-snapserver-multiroom.service
sudo systemctl enable milo-bluealsa-aplay.service
sudo systemctl enable milo-bluealsa.service
sudo systemctl start milo-backend.service
sudo systemctl start milo-frontend.service
sudo systemctl start milo-kiosk.service
sudo systemctl start milo-snapclient-multiroom.service
sudo systemctl start milo-snapserver-multiroom.service
sudo systemctl start milo-bluealsa-aplay.service
sudo systemctl start milo-bluealsa.service
```


**INTÉGRÉ DANS Milo : Commande pour faire passer toutes les sources audio "snapserver" sur "Multiroom".**

```bash
curl -s http://localhost:1780/jsonrpc -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | grep -o '"id":"[a-f0-9-]*","muted"' | cut -d'"' -f4 | while read group_id; do curl -s http://localhost:1780/jsonrpc -d "{\"id\":1,\"jsonrpc\":\"2.0\",\"method\":\"Group.SetStream\",\"params\":{\"id\":\"$group_id\",\"stream_id\":\"Multiroom\"}}"; echo "→ $group_id switched"; done
```