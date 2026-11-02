# backend/tests/architecture/test_multiroom_capture_map.py
"""Pin the backend's source -> snapserver stream table to the captures the image ships.

In multiroom every source writes its own Loopback substream, snapserver captures
each one as a named stream, and the backend points every group at the stream of
the source it starts (`Group.SetStream`). The names and devices are declared
twice: `MULTIROOM_STREAMS` in `config/constants.py`, and the `source = alsa:///`
lines `provisioning/snapcast.sh` writes into snapserver.conf at image build. The
shell runs inside a cloned pi-gen checkout that cannot import Python, so the two
agree only by being checked here. A name the table spells differently is a
`Group.SetStream` snapserver refuses: the rooms stay on the previous stream and
the new source plays to nobody, with every screen showing it playing.
"""
import re
from pathlib import Path

from backend.config.constants import MULTIROOM_STREAMS
from backend.core.models.audio_state import AudioSource

PROVISIONING = Path(__file__).resolve().parents[3] / "provisioning" / "snapcast.sh"

_CAPTURE = re.compile(r"^source\s*=\s*alsa:///\?name=([^&\s]+)&device=([^&\s]+)", re.MULTILINE)


def _shipped_captures():
    """The `(name, device)` of every ALSA capture the heredoc declares, in order.

    Read literally rather than run: the script builds an image.
    """
    return _CAPTURE.findall(PROVISIONING.read_text(encoding="utf-8"))


def test_the_stream_map_names_every_capture_of_snapcast_sh():
    """Every source but NONE has exactly the stream and the device snapserver captures."""
    shipped = _shipped_captures()
    assert len(shipped) >= 10, f"only parsed {shipped} from {PROVISIONING}"

    assert set(MULTIROOM_STREAMS) == set(AudioSource) - {AudioSource.NONE}
    assert [(s.name, s.device) for s in MULTIROOM_STREAMS.values()] == shipped
