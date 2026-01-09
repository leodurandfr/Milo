# Source Tree Analysis

> Generated: 2026-01-09 | Project: Milo Multiroom Audio System

## Repository Structure Overview

```
milo/                              # Project root
├── backend/                       # FastAPI Python backend (Part: backend)
├── frontend/                      # Vue 3 SPA frontend (Part: frontend)
├── milo-client/                   # Satellite client (Part: milo-client)
├── system/                        # Systemd service definitions
├── rootfs/                        # System files deployed at install
├── install/                       # Installation scripts
├── docs/                          # Documentation
├── CLAUDE.md                      # AI assistant guide (21KB)
├── README.md                      # Project readme
├── INSTALLATION.md                # Installation guide
├── requirements.txt               # Python dependencies
├── install.sh                     # Main installer (54KB)
└── LICENSE                        # GPL-3.0 license
```

---

## Backend Structure (`backend/`)

```
backend/
├── main.py                        # 🚀 Entry point - FastAPI app
├── config/
│   ├── container.py               # 🔧 DI container (dependency-injector)
│   └── constants.py               # Configuration constants
│
├── domain/                        # 📦 Business models (DDD)
│   ├── audio_state.py             # AudioSource, PluginState, SystemAudioState
│   ├── audio_routing.py           # Routing models
│   ├── client_registry.py         # RegisteredClient, Zone, RegistryState
│   ├── volume.py                  # Volume models
│   ├── volume_state.py            # Volume state models
│   └── exceptions.py              # Domain exceptions
│
├── application/
│   └── interfaces/
│       └── audio_source.py        # 🔌 AudioSourcePlugin interface
│
├── infrastructure/
│   ├── plugins/                   # Audio source implementations
│   │   ├── base.py                # UnifiedAudioPlugin base class
│   │   ├── plugin_utils.py        # Shared plugin utilities
│   │   ├── spotify/               # Spotify Connect (go-librespot)
│   │   │   └── plugin.py
│   │   ├── bluetooth/             # Bluetooth audio (BlueALSA)
│   │   │   ├── plugin.py
│   │   │   ├── agent.py
│   │   │   ├── bluealsa_monitor.py
│   │   │   └── bluealsa_playback.py
│   │   ├── mac/                   # Mac streaming (ROC)
│   │   │   └── plugin.py
│   │   ├── radio/                 # Internet radio (mpv)
│   │   │   ├── plugin.py
│   │   │   ├── mpv_controller.py
│   │   │   ├── station_manager.py
│   │   │   ├── radio_browser_api.py
│   │   │   ├── image_manager.py
│   │   │   └── genres.py
│   │   └── podcast/               # Podcasts (mpv + Taddy API)
│   │       ├── plugin.py
│   │       └── taddy_api.py
│   │
│   ├── services/                  # Business services
│   │   ├── settings_service.py    # Settings persistence
│   │   ├── snapcast_service.py    # Snapcast control
│   │   ├── snapcast_websocket_service.py
│   │   ├── client_registry_service.py  # Client/zone management
│   │   ├── systemd_manager.py     # Service control (PolicyKit)
│   │   ├── hardware_service.py    # Hardware configuration
│   │   ├── radio_data_service.py  # Radio persistence
│   │   ├── podcast_data_service.py # Podcast persistence
│   │   ├── program_version_service.py
│   │   ├── program_update_service.py
│   │   ├── satellite_program_update_service.py
│   │   ├── routing/               # Audio routing
│   │   │   ├── audio_routing_service.py
│   │   │   └── routing_transitions.py
│   │   ├── volume/                # Volume control
│   │   │   ├── volume_service.py
│   │   │   ├── volume_state.py
│   │   │   ├── volume_config.py
│   │   │   └── dsp_controller.py
│   │   └── dsp/                   # CamillaDSP integration
│   │       ├── camilladsp_service.py
│   │       ├── camilladsp_config.py
│   │       ├── crossover_service.py
│   │       ├── client_proxy_service.py
│   │       └── settings_sync_service.py
│   │
│   ├── hardware/                  # Hardware controllers
│   │   ├── rotary_volume_controller.py  # GPIO rotary encoder
│   │   └── screen_controller.py   # Screen brightness/timeout
│   │
│   └── state/
│       └── state_machine.py       # 🎯 UnifiedAudioStateMachine
│
├── presentation/
│   ├── api/
│   │   ├── models.py              # Pydantic request/response models
│   │   └── routes/                # REST endpoints
│   │       ├── audio.py           # /api/audio/*
│   │       ├── volume.py          # /api/volume/*
│   │       ├── routing.py         # /api/routing/*
│   │       ├── dsp.py             # /api/dsp/* (54KB - largest)
│   │       ├── spotify.py         # /spotify/*
│   │       ├── bluetooth.py       # /bluetooth/*
│   │       ├── mac.py             # /roc/*
│   │       ├── radio.py           # /api/radio/*
│   │       ├── podcast.py         # /api/podcast/*
│   │       ├── snapcast.py        # /api/snapcast/*
│   │       ├── registry.py        # /api/registry/*
│   │       ├── settings.py        # /api/settings/*
│   │       ├── programs.py        # /api/programs/*
│   │       └── health.py          # /api/health, /api/ping
│   │
│   └── websockets/
│       ├── server.py              # WebSocket endpoint handler
│       ├── manager.py             # Connection management
│       └── events.py              # Event definitions
│
└── tests/                         # pytest test suite
    ├── conftest.py                # Test fixtures
    ├── test_*.py                  # Unit tests
    └── README.md
```

---

## Frontend Structure (`frontend/`)

```
frontend/
├── index.html                     # HTML entry point
├── package.json                   # npm dependencies
├── vite.config.js                 # Vite configuration (proxy setup)
│
├── src/
│   ├── main.js                    # 🚀 Vue app entry point
│   ├── App.vue                    # Root component (7KB)
│   │
│   ├── stores/                    # 📦 Pinia state management
│   │   ├── unifiedAudioStore.js   # Audio state (source, volume)
│   │   ├── dspStore.js            # DSP state (47KB - largest)
│   │   ├── radioStore.js          # Radio state
│   │   ├── podcastStore.js        # Podcast state
│   │   ├── multiroomStore.js      # Multiroom state
│   │   ├── clientRegistryStore.js # Client registry
│   │   └── settingsStore.js       # Settings state
│   │
│   ├── components/
│   │   ├── audio/                 # Core audio UI
│   │   │   ├── AudioPlayer.vue
│   │   │   ├── AudioSourceView.vue
│   │   │   ├── AudioSourceLayout.vue
│   │   │   └── AudioSourceStatus.vue
│   │   │
│   │   ├── spotify/               # Spotify UI
│   │   │   ├── SpotifySource.vue
│   │   │   ├── PlaybackControls.vue
│   │   │   ├── ProgressBar.vue
│   │   │   ├── usePlaybackProgress.js
│   │   │   └── useSpotifyControl.js
│   │   │
│   │   ├── radio/                 # Radio UI
│   │   │   ├── RadioSource.vue
│   │   │   ├── FavoritesView.vue
│   │   │   ├── SearchView.vue
│   │   │   ├── RadioScreensaver.vue
│   │   │   └── SkeletonStationCard.vue
│   │   │
│   │   ├── podcasts/              # Podcast UI (15 components)
│   │   │   ├── PodcastSource.vue
│   │   │   ├── HomeView.vue
│   │   │   ├── SearchView.vue
│   │   │   ├── SubscriptionsView.vue
│   │   │   ├── GenreView.vue
│   │   │   ├── QueueView.vue
│   │   │   ├── PodcastCard.vue
│   │   │   ├── EpisodeCard.vue
│   │   │   ├── PodcastDetails.vue
│   │   │   ├── EpisodeDetails.vue
│   │   │   ├── ProgressBar.vue
│   │   │   └── Skeleton*.vue      # Loading skeletons
│   │   │
│   │   ├── multiroom/             # Multiroom UI
│   │   │   ├── MultiroomControl.vue
│   │   │   ├── MultiroomModal.vue
│   │   │   ├── MultiroomItem.vue
│   │   │   └── ClientEdit.vue
│   │   │
│   │   ├── settings/              # Settings UI
│   │   │   ├── SettingsModal.vue
│   │   │   ├── SettingsCategory.vue
│   │   │   └── categories/
│   │   │       ├── ApplicationsSettings.vue
│   │   │       ├── DspSettings.vue
│   │   │       ├── InfoSettings.vue
│   │   │       ├── LanguageSettings.vue
│   │   │       ├── MultiroomSettings.vue
│   │   │       ├── PodcastSettings.vue
│   │   │       ├── ScreenSettings.vue
│   │   │       ├── SpotifySettings.vue
│   │   │       ├── UpdateManager.vue
│   │   │       ├── VolumeSettings.vue
│   │   │       ├── dsp/           # DSP controls (9 components)
│   │   │       └── radio/         # Radio settings
│   │   │
│   │   └── ui/                    # Reusable UI components (17)
│   │       ├── Button.vue
│   │       ├── Modal.vue
│   │       ├── Toggle.vue
│   │       ├── Dropdown.vue
│   │       ├── RangeSlider.vue
│   │       ├── VolumeBar.vue
│   │       ├── Dock.vue
│   │       └── ...
│   │
│   ├── composables/               # Vue composables
│   │   ├── useAnimatedHeight.js
│   │   ├── useHardwareConfig.js
│   │   ├── useNavigationStack.js
│   │   ├── useScreenActivity.js
│   │   ├── useSettingsAPI.js
│   │   └── useVirtualKeyboard.js
│   │
│   ├── services/                  # Services
│   │   ├── websocket.js           # WebSocket client
│   │   ├── i18n.js                # Internationalization
│   │   └── logger.js              # Logging service
│   │
│   ├── schemas/                   # Zod validation schemas
│   │   └── api.js
│   │
│   ├── locales/                   # i18n translations
│   │   ├── en.json
│   │   └── fr.json
│   │
│   ├── router/                    # Vue Router
│   │   └── index.js
│   │
│   ├── views/                     # Page views
│   │   └── MainView.vue
│   │
│   ├── constants/                 # App constants
│   ├── directives/                # Vue directives
│   │
│   └── assets/                    # Static assets
│       ├── icons/
│       ├── app-icons/
│       ├── settings-icons/
│       ├── flags-icons/
│       ├── fonts/
│       ├── styles/
│       ├── radio/
│       └── podcasts/genres/
│
├── public/                        # Public static files
└── tests/                         # Vitest tests
    ├── stores/
    └── schemas/
```

---

## Satellite Client Structure (`milo-client/`)

```
milo-client/
├── install-client.sh              # Client installer (27KB)
├── app/
│   ├── main.py                    # 🚀 FastAPI satellite app (52KB)
│   └── requirements.txt           # Python dependencies
│
├── system/                        # Systemd services
│   └── README.md
│
├── configs/
│   └── camilladsp/                # CamillaDSP configurations
│
└── rootfs/                        # System files for client
    ├── etc/
    │   ├── avahi/                 # mDNS configuration
    │   ├── modprobe.d/            # Kernel module config
    │   ├── sudoers.d/             # Sudo permissions
    │   └── NetworkManager/
    │       └── dispatcher.d/      # Network event scripts
    └── usr/local/bin/             # Utility scripts
```

---

## System Files Structure

### Systemd Services (`system/`)
```
system/
├── milo-backend.service           # FastAPI backend
├── milo-camilladsp.service        # DSP engine
├── milo-spotify.service           # Spotify Connect
├── milo-bluealsa.service          # Bluetooth service
├── milo-bluealsa-aplay.service    # Bluetooth playback
├── milo-mac.service               # Mac streaming (ROC)
├── milo-radio.service             # Radio (mpv)
├── milo-podcast.service           # Podcast (mpv)
├── milo-snapserver-multiroom.service
├── milo-snapclient-multiroom.service
├── milo-kiosk.service             # Chromium kiosk
├── milo-readiness.service         # System readiness
├── milo-disable-wifi-power-management.service
└── README.md
```

### Root Filesystem (`rootfs/`)
```
rootfs/
├── var/lib/milo/                  # Runtime data directory
│   └── camilladsp/                # DSP configurations
├── home/milo/
│   └── .config/                   # User configuration
├── etc/
│   ├── avahi/                     # mDNS service definition
│   └── NetworkManager/
│       └── dispatcher.d/          # Network event scripts
└── usr/
    ├── local/bin/                 # System scripts
    │   └── milo-wait-ready.sh
    └── share/plymouth/
        └── themes/milo/           # Boot animation
```

---

## Critical Entry Points

| Component | Entry Point | Description |
|-----------|-------------|-------------|
| Backend | `backend/main.py` | FastAPI app initialization |
| Frontend | `frontend/src/main.js` | Vue app bootstrap |
| Client | `milo-client/app/main.py` | Satellite FastAPI app |
| Installer | `install.sh` | Main installation script |

---

## Integration Points

```
┌─────────────────┐     HTTP/WS      ┌─────────────────┐
│    Frontend     │ ◄──────────────► │    Backend      │
│   (Vue 3/SPA)   │   localhost:5173 │   (FastAPI)     │
│   Port: 5173    │   proxy to :8000 │   Port: 8000    │
└─────────────────┘                  └────────┬────────┘
                                              │
        ┌─────────────────────────────────────┼─────────────────────────────────────┐
        │                                     │                                     │
        ▼                                     ▼                                     ▼
┌───────────────┐                   ┌─────────────────┐                   ┌─────────────────┐
│   Snapcast    │ ◄───────────────► │   CamillaDSP    │                   │  Milo-Client    │
│   (Multiroom) │    audio stream   │   (DSP Engine)  │                   │  (Satellites)   │
│   Port: 1704  │                   │   Port: 1234    │                   │   Port: 8001    │
└───────────────┘                   └─────────────────┘                   └─────────────────┘
```
