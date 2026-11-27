"""The logs page's reader — see service.py for what it reads and how every
line is redacted before it leaves."""
from backend.core.system.logs.service import LogService

__all__ = ["LogService"]
