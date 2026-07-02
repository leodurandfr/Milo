"""Network management service (Ethernet + WiFi) using NetworkManager.

No eager service re-export here: `core/models/ws_events.py` imports
`core.network.models` (NetworkStatus embeds into NetworkStatusChanged), and an
eager `from .service import NetworkService` would close an import cycle
(service → ws_events → this package). Import the service by full path:
`backend.core.network.service.NetworkService`.
"""
