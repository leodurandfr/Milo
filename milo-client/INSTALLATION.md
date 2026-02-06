# Milo Client - Installation Guide

## Environment Variables Configuration

The Milo Client requires environment variables to be configured in `/var/lib/milo-client/env`.

### Required Variables

```bash
# Principal Milo server IP address
MILO_PRINCIPAL_IP=192.168.1.73

# DSP configuration (usually false for clients)
MILO_CLIENT_DSP_ENABLED=false
```

### Optional Performance Variables

For optimized low-latency multiroom audio (LAN environments):

```bash
# Snapclient ALSA buffer configuration
# Lower values = lower latency, but may cause audio dropouts on unstable networks
# Recommended values:
#   - LAN (Ethernet): 20ms
#   - WiFi: 40-80ms (depends on network quality)
MILO_SNAPCLIENT_BUFFER_TIME=20

# ALSA buffer fragments (usually 4)
MILO_SNAPCLIENT_FRAGMENTS=4
```

### Complete Example

Example `/var/lib/milo-client/env` file for LAN low-latency configuration:

```bash
MILO_PRINCIPAL_IP=192.168.1.73
MILO_CLIENT_DSP_ENABLED=false
MILO_SNAPCLIENT_BUFFER_TIME=20
MILO_SNAPCLIENT_FRAGMENTS=4
```

## Network Considerations

- **LAN (Ethernet)**: Use `MILO_SNAPCLIENT_BUFFER_TIME=20` for minimal latency (~100ms total end-to-end)
- **WiFi**: Use `MILO_SNAPCLIENT_BUFFER_TIME=40-80` for stability (network jitter compensation)
- **Default (if not specified)**: 80ms (balanced for WiFi/LAN)

## Systemd Services

The Milo Client uses the following systemd services:

- `milo-client.service` - Main API service
- `milo-client-snapclient.service` - Snapcast audio client
- `milo-client-camilladsp.service` - Audio processing (DSP)

All services will automatically read the environment variables from `/var/lib/milo-client/env`.

## Applying Changes

After modifying `/var/lib/milo-client/env`, restart the affected services:

```bash
sudo systemctl daemon-reload
sudo systemctl restart milo-client-snapclient.service
```

## Verification

Check that the snapclient is running with the correct buffer settings:

```bash
ps aux | grep snapclient
```

You should see:
```
--player alsa:buffer_time=20,fragments=4
```

Check the service logs:

```bash
sudo journalctl -u milo-client-snapclient -f
```

Look for lines like:
```
(Alsa) Using configured buffer_time: 20 ms, configured fragments: 4
```
