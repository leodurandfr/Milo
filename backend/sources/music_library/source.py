# backend/sources/music_library/source.py
"""Music Library audio source (Family C — active player).

Plays the user's own music, indexed by a Navidrome sidecar and streamed to mpv
over localhost HTTP (mirrors the Podcast source, which streams from Podcast
Index). Controlled from Milō's UI, with rich metadata (artwork/title/artist).

Phase 0 skeleton: it exists only so the source registers and shows up in the
dock. The ALSA trio and milo-music-library.service now exist (P0-2), but this
skeleton does NOT start mpv or produce audio yet — the Navidrome client arrives
in P0-3 and play_context / queue / transport in P1-6. Until then, selecting the
source lands on the WAITING placeholder status card. See docs/plans/music-library.md.
"""
from typing import Any, Dict, Optional

from backend.shared.mpv_audio_source import MpvAudioSource


class MusicLibrarySource(MpvAudioSource):
    """Music Library source (Family C). Phase 0 skeleton — no playback yet."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
    ):
        super().__init__(
            source_id="music_library",
            service_name="milo-music-library.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )

    async def _do_start(self) -> bool:
        """Phase 0: present an idle placeholder without touching audio.

        The ALSA trio and milo-music-library.service exist (P0-2), but the
        skeleton doesn't connect to mpv yet — real startup is layered in from
        P0-3 (Navidrome client) onward. Emit a WAITING state so selecting the
        source lands on the status card.
        """
        self.emit_connection_state(False)
        return True

    async def _do_stop(self) -> bool:
        """Phase 0: nothing to tear down (no service or mpv running)."""
        return True
