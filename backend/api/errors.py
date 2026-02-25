"""API endpoint to receive frontend error reports and persist them to errors.log."""
import logging
from fastapi import APIRouter
from pydantic import BaseModel

_frontend_logger = logging.getLogger("frontend")


class FrontendError(BaseModel):
    source: str
    error: str
    info: str = ""


def create_errors_router() -> APIRouter:
    router = APIRouter(prefix="/api/errors", tags=["errors"])

    @router.post("")
    async def report_frontend_error(payload: FrontendError):
        message = f"[{payload.source}] {payload.error}"
        if payload.info:
            message += f"\n{payload.info}"
        _frontend_logger.error(message)
        return {"status": "ok"}

    return router
