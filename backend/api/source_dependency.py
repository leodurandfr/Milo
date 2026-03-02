# backend/api/source_dependency.py
"""
Shared factory for audio plugin route source dependencies.

Eliminates the repeated source_provider boilerplate pattern
across all 6 plugin routes.py files (~10 lines each).
"""
from typing import Callable, TypeVar

from fastapi import HTTPException

T = TypeVar('T')


def make_source_dependency(name: str):
    """
    Create source provider boilerplate for an audio plugin router.

    Returns (set_provider, get_source) tuple:
    - set_provider: call with source_provider callable to configure
    - get_source: FastAPI Depends() dependency that returns the source
    """
    _holder = [None]

    def set_provider(source_provider: Callable[[], T]) -> None:
        _holder[0] = source_provider

    def get_source() -> T:
        if _holder[0] is None:
            raise HTTPException(status_code=503, detail=f"{name} source not configured")
        source = _holder[0]()
        if source is None:
            raise HTTPException(status_code=503, detail=f"{name} source not available")
        return source

    return set_provider, get_source
