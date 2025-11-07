"""
Minimalist utility functions for audio plugins.
"""
import logging
import asyncio
from typing import Dict, Any, Callable, Awaitable, Tuple, Optional, TypeVar

T = TypeVar('T')

def format_response(success: bool, message: str = None, error: str = None, **kwargs) -> Dict[str, Any]:
    """Formats a standardized response."""
    response = {"success": success}
    
    if success and message:
        response["message"] = message
    elif not success and error:
        response["error"] = error
    
    return {**response, **kwargs}

async def safely_execute(logger: logging.Logger,
                         func: Callable[..., Awaitable[T]],
                         *args, **kwargs) -> Tuple[bool, Optional[T], Optional[str]]:
    """Executes a function safely."""
    try:
        result = await func(*args, **kwargs)
        return True, result, None
    except Exception as e:
        logger.error(f"Error: {e}")
        return False, None, str(e)

class WebSocketManager:
    """Simplified manager for WebSocket connections."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.connected = False
        self.task = None
        self._stopping = False

    async def start(self, connect_func: Callable[[], Awaitable[bool]],
                   process_func: Callable[[], Awaitable[None]]) -> None:
        """
        Starts the WebSocket connection.

        Args:
            connect_func: Function that establishes the initial connection
            process_func: Function that processes messages
        """
        await self.stop()

        self._stopping = False

        async def connection_loop():
            try:
                if await connect_func():
                    self.connected = True
                    await process_func()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
            finally:
                self.connected = False

        self.task = asyncio.create_task(connection_loop())

    async def stop(self) -> None:
        """Stops the WebSocket connection."""
        self._stopping = True

        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error(f"WebSocket stop error: {e}")
            finally:
                self.task = None

        self.connected = False