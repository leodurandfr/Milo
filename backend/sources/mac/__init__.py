# backend/sources/mac/__init__.py
"""Mac audio source via the ROC toolkit (Family A — mute receiver).

Streams audio from Mac computers over ROC, several senders at once. No rich
metadata is exposed; control flows through the generic
`/api/audio/control/mac` endpoint — no dedicated routes.py.
"""
from backend.sources.mac.source import MacSource

__all__ = ["MacSource"]
