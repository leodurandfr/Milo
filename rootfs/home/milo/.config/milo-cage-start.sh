#!/bin/bash
# Milo Kiosk - Launch Cage with Chromium in fullscreen
# Services are guaranteed to be ready by milo-readiness.service

echo "Starting Milo Kiosk mode..."

# Hide cursor using Adwaita (modified with transparent cursors)
export XCURSOR_THEME=Adwaita
export XCURSOR_SIZE=24
export WLR_XCURSOR_THEME=Adwaita
export WLR_XCURSOR_SIZE=24

# Launch Cage with Chromium in kiosk mode
exec cage -- /usr/bin/chromium \
  --kiosk \
  --incognito \
  --password-store=basic \
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
  --touch-events=enabled \
  --enable-features=TouchpadAndWheelScrollLatching \
  --force-device-scale-factor=1 \
  --disable-pinch \
  --disable-features=VizDisplayCompositor \
  --app=http://localhost
