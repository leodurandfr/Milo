# Development Guide

> Generated: 2026-01-09 | Project: Milo

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | Backend (FastAPI) |
| Node.js | 18+ | Frontend (Vue 3) |
| npm | 9+ | Package management |
| Raspberry Pi OS | 64-bit Lite (Trixie) | Target platform |

---

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/leodurandfr/Milo.git
cd milo
```

### 2. Backend Setup
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run backend (development)
cd backend
python main.py
# Backend runs on http://0.0.0.0:8000
```

### 3. Frontend Setup
```bash
cd frontend

# Install dependencies
npm install

# Run frontend (development)
npm run dev
# Frontend runs on http://0.0.0.0:5173
# API requests proxied to backend via vite.config.js
```

---

## Development Commands

### Backend

| Command | Description |
|---------|-------------|
| `python main.py` | Run backend server |
| `python -m pytest` | Run all tests |
| `python -m pytest -v` | Run tests (verbose) |
| `python -m pytest -k "test_name"` | Run specific test |
| `python -m pytest --cov=backend` | Run with coverage |

### Frontend

| Command | Description |
|---------|-------------|
| `npm run dev` | Start dev server with HMR |
| `npm run build` | Build for production |
| `npm run preview` | Preview production build |
| `npm run test` | Run Vitest tests |
| `npm run test:run` | Run tests once |
| `npm run test:coverage` | Run with coverage |

### Systemd Services (Production)

| Command | Description |
|---------|-------------|
| `sudo systemctl status milo-backend` | Check backend status |
| `sudo systemctl restart milo-backend` | Restart backend |
| `sudo journalctl -u milo-backend -f` | View live logs |
| `sudo systemctl restart milo-frontend` | Restart frontend |

---

## Project Structure

```
milo/
├── backend/              # FastAPI backend
│   ├── main.py           # Entry point
│   ├── dependencies.py   # Service Registry (lazy singletons)
│   ├── core/             # Core infrastructure (state, events, services)
│   ├── sources/          # Audio source implementations (spotify, bluetooth, etc.)
│   ├── api/              # REST API routes
│   ├── ws/               # WebSocket server
│   ├── hardware/         # Hardware controllers
│   ├── shared/           # Shared utilities
│   └── tests/            # pytest tests
│
├── frontend/             # Vue 3 frontend
│   ├── src/
│   │   ├── stores/       # Pinia stores
│   │   ├── components/   # Vue components
│   │   ├── composables/
│   │   └── services/     # WebSocket, i18n
│   └── tests/            # Vitest tests
│
├── milo-client/          # Satellite client
├── system/               # Systemd services
└── rootfs/               # System files
```

---

## Testing

### Backend Tests (pytest)

```bash
# Activate virtual environment
source venv/bin/activate

# Run all tests
python -m pytest backend/tests/

# Run with verbose output
python -m pytest backend/tests/ -v

# Run specific test file
python -m pytest backend/tests/test_state_machine.py

# Run specific test
python -m pytest backend/tests/test_state_machine.py::TestAudioStateMachine::test_initialization

# Coverage report
python -m pytest backend/tests/ --cov=backend --cov-report=term-missing

# HTML coverage report
python -m pytest backend/tests/ --cov=backend --cov-report=html
# Open: htmlcov/index.html
```

**Current test count:** 40 tests passing

### Frontend Tests (Vitest)

```bash
cd frontend

# Run tests in watch mode
npm run test

# Run tests once
npm run test:run

# With coverage
npm run test:coverage
```

---

## Environment Variables

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `MILO_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `MILO_MODE` | `direct` | Audio mode (direct, multiroom) |

### Frontend

Vite proxy configuration in `vite.config.js`:
- `/api` → `http://127.0.0.1:8000`
- `/ws` → `ws://127.0.0.1:8000`
- `/spotify`, `/roc`, `/librespot` → `http://127.0.0.1:8000`

---

## Code Style

### Python (Backend)

- **Async/await everywhere** for I/O operations
- **Type hints** for function signatures
- **Docstrings** for public methods
- **Feature-based architecture** (core/, features/, api/)

### JavaScript/Vue (Frontend)

- **Composition API** for components
- **Pinia** for state management
- **Zod** for schema validation
- **Comments in English**

---

## Adding New Features

### New Audio Source

1. Create enum in `backend/core/models/audio_state.py`:
   ```python
   class AudioSource(Enum):
       NEW_SOURCE = "new_source"
   ```

2. Create feature module in `backend/features/new_source/`:
   ```python
   # source.py
   class NewSourceSource(BaseAudioSource):
       async def initialize(self) -> bool: ...
       async def start(self) -> bool: ...
       async def stop(self) -> bool: ...

   # routes.py - FastAPI routes for this source
   ```

3. Register in `backend/dependencies.py`

4. Add ALSA devices in `/etc/asound.conf`

5. Create API routes in `backend/features/new_source/routes.py`

6. Create Vue component in `frontend/src/components/new_source/`

7. Update Pinia store if needed

### New API Endpoint

1. Create route file or add to existing in `backend/api/` or `backend/features/<feature>/routes.py`
2. Register router in `backend/main.py`
3. Update frontend store to call new endpoint
4. Add Zod schema for response validation

### New UI Component

1. Create component in appropriate folder under `frontend/src/components/`
2. Follow Vue 3 Composition API patterns
3. Use existing UI primitives from `components/ui/`
4. Add translations to `locales/en.json` and `locales/fr.json`

---

## Debugging

### Backend

```bash
# View live logs
sudo journalctl -u milo-backend -f

# Enable debug logging
export MILO_LOG_LEVEL=DEBUG
python main.py

# Python debugger
import pdb; pdb.set_trace()
```

### Frontend

- **Vue DevTools** browser extension
- **Console** for WebSocket events
- **Network tab** for API calls
- **Vite HMR** for hot reloading

---

## Common Issues

### Backend won't start
1. Check virtual environment is activated
2. Verify all dependencies installed
3. Check port 8000 not in use
4. View logs: `sudo journalctl -u milo-backend -f`

### Frontend proxy errors
1. Ensure backend is running on port 8000
2. Check `vite.config.js` proxy configuration
3. Verify CORS settings in `backend/main.py`

### WebSocket not connecting
1. Check backend is running
2. Verify `/ws` proxy in vite config
3. Check browser console for errors

### Tests failing
1. Ensure virtual environment activated
2. Run `pip install -r requirements.txt`
3. Check for async test markers (`@pytest.mark.asyncio`)

---

## Deployment

### Production Build

```bash
# Backend runs via systemd
sudo systemctl enable milo-backend
sudo systemctl start milo-backend

# Frontend build
cd frontend
npm run build
# Output: frontend/dist/

# Serve via nginx or milo-frontend service
sudo systemctl enable milo-frontend
sudo systemctl start milo-frontend
```

### Service Management

```bash
# All Milo services
sudo systemctl status 'milo-*'

# Restart all
sudo systemctl restart milo-backend milo-frontend

# View all logs
sudo journalctl -u 'milo-*' -f
```

---

## Resources

- [Architecture Overview](architecture-overview.md)
- [API Contracts](api-contracts-backend.md)
- [Component Inventory](component-inventory-frontend.md)
- [Source Tree Analysis](source-tree-analysis.md)
