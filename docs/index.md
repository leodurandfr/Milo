# Milo Documentation Index

> **Project:** Milo Multiroom Audio System
> **Generated:** 2026-01-09
> **Scan Level:** Deep

---

## Quick Links

| Need to... | Go to |
|------------|-------|
| Understand the architecture | [Architecture Overview](architecture-overview.md) |
| Set up development | [Development Guide](development-guide.md) |
| Find an API endpoint | [API Contracts](api-contracts-backend.md) |
| Find a Vue component | [Component Inventory](component-inventory-frontend.md) |
| Understand data models | [Data Models](data-models-backend.md) |
| See directory structure | [Source Tree](source-tree-analysis.md) |
| Understand integrations | [Integration Architecture](integration-architecture.md) |

---

## Documentation Map

### Core Documentation

| Document | Description | Audience |
|----------|-------------|----------|
| [Architecture Overview](architecture-overview.md) | High-level system architecture, principles, patterns | All developers |
| [Integration Architecture](integration-architecture.md) | Multi-part communication, data flows, protocols | Backend/Integration |
| [Development Guide](development-guide.md) | Setup, commands, debugging, workflow | New developers |

### Technical Reference

| Document | Description | Audience |
|----------|-------------|----------|
| [API Contracts](api-contracts-backend.md) | 180+ REST endpoints, WebSocket events | Frontend/API consumers |
| [Component Inventory](component-inventory-frontend.md) | 77 Vue components, stores, composables | Frontend developers |
| [Data Models](data-models-backend.md) | Domain models, persistence, state | Backend developers |
| [Source Tree](source-tree-analysis.md) | Directory structure, entry points | All developers |

### Existing Documentation

| Document | Description |
|----------|-------------|
| [README.md](../README.md) | Project overview, installation |
| [INSTALLATION.md](../INSTALLATION.md) | Detailed installation guide |
| [CLAUDE.md](../CLAUDE.md) | AI assistant guide (comprehensive) |
| [architecture.md](architecture.md) | Original architecture doc |
| [development.md](development.md) | Original development guide |

---

## Project Summary

### What is Milo?

A multiroom audio system for Raspberry Pi supporting:
- **Spotify Connect** - Native Spotify playback
- **Bluetooth** - Any Bluetooth audio source
- **Mac Streaming** - ROC-based Mac audio streaming
- **Internet Radio** - 50,000+ stations via RadioBrowser
- **Podcasts** - Search, subscribe, resume via Taddy API

### Architecture at a Glance

```
┌─────────────┐     REST/WS      ┌─────────────┐
│  Frontend   │ ◄──────────────► │   Backend   │
│   Vue 3     │                  │   FastAPI   │
│  Port 5173  │                  │  Port 8000  │
└─────────────┘                  └──────┬──────┘
                                        │
            ┌───────────────────────────┼───────────────────────────┐
            │                           │                           │
            ▼                           ▼                           ▼
     ┌─────────────┐            ┌─────────────┐            ┌─────────────┐
     │ CamillaDSP  │            │  Snapcast   │            │   Plugins   │
     │   (DSP)     │            │ (Multiroom) │            │ (5 sources) │
     └─────────────┘            └─────────────┘            └─────────────┘
```

### Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3, Pinia, Vite, axios, Zod |
| Backend | FastAPI, Pydantic, dependency-injector |
| Audio | ALSA, CamillaDSP, Snapcast, mpv |
| Infrastructure | systemd, Raspberry Pi OS |

---

## Key Concepts

### State Machine
The `UnifiedAudioStateMachine` is the single source of truth for audio state. All changes go through it.

### Plugin System
Audio sources implement `AudioSourcePlugin` interface. Each manages its own systemd service.

### WebSocket Sync
All state changes broadcast via WebSocket. Frontend reacts, never polls.

### Volume Control
Unified dB-based volume system. Direct mode uses ALSA, multiroom uses CamillaDSP per-client.

### Client Registry
Centralized registry of all multiroom clients and zones for coordinated control.

---

## File Counts

| Category | Count |
|----------|-------|
| Backend Python files | ~90 |
| Frontend Vue components | 77 |
| Pinia stores | 7 |
| REST API endpoints | 180+ |
| Systemd services | 14 |
| Backend tests | 40 |

---

## Getting Started

### For New Developers

1. Read [Architecture Overview](architecture-overview.md)
2. Follow [Development Guide](development-guide.md) setup
3. Explore [Source Tree](source-tree-analysis.md)

### For API Work

1. Reference [API Contracts](api-contracts-backend.md)
2. Check [Data Models](data-models-backend.md)
3. Review WebSocket events in [Integration Architecture](integration-architecture.md)

### For Frontend Work

1. Browse [Component Inventory](component-inventory-frontend.md)
2. Understand stores in [Architecture Overview](architecture-overview.md)
3. Check existing patterns in `frontend/src/components/`

### For Integration Work

1. Study [Integration Architecture](integration-architecture.md)
2. Review service dependencies in [Architecture Overview](architecture-overview.md)
3. Check systemd services in `system/`

---

## Document Generation Info

| Metadata | Value |
|----------|-------|
| Workflow | document-project (BMAD) |
| Mode | Initial Scan |
| Scan Level | Deep |
| Generated | 2026-01-09 |
| Documents Created | 8 |

### Generated Documents

1. `index.md` - This file
2. `architecture-overview.md` - Main architecture
3. `integration-architecture.md` - Multi-part integration
4. `development-guide.md` - Developer setup
5. `api-contracts-backend.md` - REST API reference
6. `component-inventory-frontend.md` - Vue components
7. `data-models-backend.md` - Domain models
8. `source-tree-analysis.md` - Directory structure
