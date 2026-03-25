# API Contracts - Backend

> Generated: 2026-01-09 | Scan Level: Deep

## Overview

The Milo backend exposes a comprehensive REST API organized by domain. All endpoints are prefixed appropriately and communicate via JSON.

**Base URL:** `http://milo.local:8000` (production) or `http://localhost:8000` (development)

---

## API Modules Summary

| Module | Prefix | Endpoints | Description |
|--------|--------|-----------|-------------|
| Audio | `/api/audio` | 3 | Core audio source control |
| Volume | `/api/volume` | 8 | Volume control (dB-based) |
| Routing | `/api/routing` | 6 | Multiroom & DSP routing |
| DSP | `/api/dsp` | 50+ | Equalizer, compressor, crossover |
| Spotify | `/spotify` | 6 | Spotify Connect control |
| Radio | `/api/radio` | 22 | Internet radio stations |
| Podcast | `/api/podcast` | 25 | Podcast playback & subscriptions |
| Bluetooth | `/bluetooth` | 3 | Bluetooth audio |
| Mac | `/roc` | 5 | Mac streaming (ROC) |
| Snapcast | `/api/snapcast` | 8 | Multiroom client control |
| Registry | `/api/registry` | 15 | Client/zone management |
| Settings | `/api/settings` | 30+ | System configuration |
| Programs | `/api/programs` | 12 | Version & updates |
| Health | `/api` | 2 | Health checks |

---

## Audio Control (`/api/audio`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/state` | Get current audio system state |
| POST | `/source/{source_name}` | Change active audio source |
| POST | `/control/{source_name}` | Send command to audio source |

**Audio Sources:** `none`, `spotify`, `bluetooth`, `mac`, `radio`, `podcast`

---

## Volume Control (`/api/volume`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Get volume status |
| GET | `/state` | Get full volume state |
| GET | `/` | Get current volume |
| POST | `/set` | Set absolute volume (dB) |
| POST | `/adjust` | Adjust volume by delta (dB) |
| POST | `/increase` | Increase volume by step |
| POST | `/decrease` | Decrease volume by step |
| POST | `/zone/{zone_id}/delta` | Adjust zone volume |
| GET | `/zone/{zone_id}` | Get zone volume |

---

## Routing Control (`/api/routing`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Get routing status |
| GET | `/services` | List available services |
| POST | `/multiroom/{enabled}` | Enable/disable multiroom |
| POST | `/dsp/{enabled}` | Enable/disable DSP effects |
| GET | `/multiroom/status` | Get multiroom status |
| GET | `/dsp/status` | Get DSP status |

---

## DSP Control (`/api/dsp`)

### Core DSP
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/enabled` | Check if DSP is enabled |
| PUT | `/enabled` | Enable/disable DSP |
| GET | `/status` | Get DSP status |
| GET | `/levels` | Get audio levels |
| POST | `/connect` | Connect to CamillaDSP |
| POST | `/disconnect` | Disconnect from CamillaDSP |
| POST | `/save` | Save DSP configuration |

### Parametric EQ
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/filters` | List all EQ filters |
| POST | `/filter` | Create new filter |
| PUT | `/filter/{filter_id}` | Update filter |
| DELETE | `/filter/{filter_id}` | Delete filter |
| POST | `/reset` | Reset to defaults |

### Presets
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/presets` | List presets |
| POST | `/preset` | Save preset |
| PUT | `/preset/{preset_name}` | Update preset |
| DELETE | `/preset/{preset_name}` | Delete preset |

### Effects
| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/mute` | Set mute state |
| GET | `/compressor` | Get compressor settings |
| PUT | `/compressor` | Update compressor |
| GET | `/loudness` | Get loudness settings |
| PUT | `/loudness` | Update loudness |
| GET | `/delay` | Get delay settings |
| PUT | `/delay` | Update delay |

### Zone/Link Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/targets` | List DSP targets |
| GET | `/links` | List linked groups |
| POST | `/links` | Create link group |
| DELETE | `/links/{client_id}` | Remove from link |
| DELETE | `/links` | Clear all links |
| DELETE | `/links/group/{group_id}` | Delete link group |
| PUT | `/links/{group_id}/name` | Rename link group |

### Crossover
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/crossover` | Get crossover settings |
| PUT | `/crossover` | Update crossover |
| GET | `/links/{group_id}/crossover` | Get group crossover |
| PUT | `/links/{group_id}/crossover` | Update group crossover |
| POST | `/links/{group_id}/crossover/apply` | Apply crossover |

### Client DSP (Satellites)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/client/{hostname}/status` | Get client DSP status |
| GET | `/client/{hostname}/filters` | Get client filters |
| PUT | `/client/{hostname}/filter/{filter_id}` | Update client filter |
| POST | `/client/{hostname}/reset` | Reset client DSP |
| GET/PUT | `/client/{hostname}/compressor` | Client compressor |
| GET/PUT | `/client/{hostname}/loudness` | Client loudness |
| GET/PUT | `/client/{hostname}/delay` | Client delay |
| GET/PUT | `/client/{hostname}/volume` | Client volume |
| PUT | `/client/{hostname}/mute` | Client mute |

---

## Radio (`/api/radio`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stations` | Search/list stations |
| GET | `/station/{station_id}` | Get station details |
| POST | `/play` | Play station |
| POST | `/stop` | Stop playback |
| GET | `/status` | Get playback status |
| GET | `/stats` | Get statistics |
| GET | `/countries` | List countries |
| GET | `/favorites` | Get favorites |
| POST | `/favorites/add` | Add to favorites |
| POST | `/favorites/remove` | Remove from favorites |
| POST | `/favorites/modify-metadata` | Edit favorite metadata |
| POST | `/favorites/restore-metadata` | Restore original metadata |
| POST | `/broken/mark` | Mark station as broken |
| POST | `/broken/reset` | Reset broken status |
| GET | `/custom` | List custom stations |
| POST | `/custom/add` | Add custom station |
| POST | `/custom/remove` | Remove custom station |
| PUT | `/custom/update` | Update custom station |
| POST | `/custom/update-image` | Update station image |
| POST | `/custom/remove-image` | Remove station image |
| POST | `/custom/from-favorite` | Create custom from favorite |
| GET | `/images/{filename}` | Get station image |
| GET | `/favicon` | Get favicon for URL |

---

## Podcast (`/api/podcast`)

### Discovery
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/discover/popular` | Get popular podcasts |
| GET | `/discover/top-charts` | Get top charts |
| GET | `/discover/top-charts/{country}` | Get country charts |
| GET | `/discover/genres` | List genres |
| GET | `/discover/popular-episodes` | Get popular episodes |
| GET | `/discover/by-genre` | Browse by genre |
| GET | `/lookup/itunes/{itunes_id}` | Lookup by iTunes ID |
| GET | `/search` | Search podcasts |

### Content
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/series/{uuid}` | Get podcast details |
| GET | `/episode/{uuid}` | Get episode details |

### Playback
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/play` | Play episode |
| POST | `/pause` | Pause playback |
| POST | `/resume` | Resume playback |
| POST | `/seek` | Seek to position |
| POST | `/stop` | Stop playback |
| POST | `/speed` | Set playback speed |
| GET | `/status` | Get playback status |

### Subscriptions & Queue
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/subscriptions` | List subscriptions |
| POST | `/subscriptions` | Subscribe to podcast |
| DELETE | `/subscriptions/{uuid}` | Unsubscribe |
| GET | `/subscriptions/latest-episodes` | Get latest episodes |
| GET | `/queue` | Get queue |
| POST | `/queue/{episode_uuid}/complete` | Mark complete |
| DELETE | `/queue/{episode_uuid}` | Remove from queue |

### Progress & Settings
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/progress/{episode_uuid}` | Get progress |
| POST | `/progress/{episode_uuid}` | Save progress |
| GET | `/settings` | Get podcast settings |
| POST | `/settings` | Update settings |
| GET | `/api-quota` | Check API quota |

---

## Client Registry (`/api/registry`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/state` | Get full registry state |
| GET | `/clients` | List all clients |
| GET | `/clients/available` | List available clients |
| GET | `/clients/{dsp_id}` | Get client details |
| GET | `/clients/{dsp_id}/available` | Check client availability |
| PUT | `/clients/{dsp_id}/type` | Set client speaker type |
| GET | `/clients/{dsp_id}/zone` | Get client's zone |
| GET | `/zones` | List all zones |
| GET | `/zones/{zone_id}` | Get zone details |
| POST | `/zones` | Create zone |
| PUT | `/zones/{zone_id}` | Update zone |
| DELETE | `/zones/{zone_id}` | Delete zone |
| PUT | `/zones/{zone_id}/clients` | Set zone clients |
| POST | `/zones/{zone_id}/clients/{dsp_id}` | Add client to zone |
| DELETE | `/zones/{zone_id}/clients/{dsp_id}` | Remove client from zone |
| GET | `/zones/{zone_id}/clients` | List zone clients |
| GET | `/zones/{zone_id}/clients/available` | List available zone clients |

---

## WebSocket (`/ws`)

Real-time bidirectional communication for state synchronization.

### Events Received (Server → Client)
- `initial_state` - Full state on connection
- `state_changed` - Audio state updates
- `volume_changed` - Volume updates
- `source_state_changed` - Source status changes
- `routing_changed` - Routing configuration changes
- `dsp_*` - DSP-related events
- `registry_*` - Client registry events

### Commands Sent (Client → Server)
- `subscribe` - Subscribe to event categories
- `ping` - Keep-alive

---

## Authentication

Currently no authentication required (local network only).

CORS restricted to:
- `http://milo.local`
- `https://milo.local`
- `http://localhost:5173`
- `http://127.0.0.1:5173`

---

## Rate Limiting

Default: 100 requests/minute per IP (via slowapi)
