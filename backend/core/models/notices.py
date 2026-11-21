# backend/core/models/notices.py
"""What the interface is told of a persisted file that was set aside."""
from pydantic import BaseModel, ConfigDict


class SetAsideNotice(BaseModel):
    """A file Milō could not read: it was set aside and what it held started again
    from its defaults. `file` is where it lived, `kept_as` the name its bytes are
    kept under beside it, `reason` what was wrong with it, `at` when (Unix time).

    Persisted in `notices.json` by `core/system/notices.py` until acknowledged,
    so the banner it raises outlives the boot it happened at — and the setup
    wizard a set-aside `settings.json` brings back.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    file: str
    kept_as: str
    reason: str
    at: float
