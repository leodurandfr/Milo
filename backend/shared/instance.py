"""The identity of this backend process.

Drawn once per process start, so a client can tell a backend that restarted
from a reconnect to the same one — which look identical from the socket.
"""
import uuid

SERVER_INSTANCE = uuid.uuid4().hex
