# backend/tests/architecture/test_multiroom_capture_map.py
"""Pin each source's multiroom slot to the capture snapserver reads it from.

In multiroom a source's unit writes `pcm.milo_<x>`, which asound.conf resolves to
`pcm.milo_<x>_multiroom`: one Loopback substream. snapserver captures the other
end of that substream as the source's named stream, and the backend points every
group at it (`Group.SetStream`). Four files declare a part of that chain, and
only one of them is Python:

* `MULTIROOM_STREAMS` (`config/constants.py`), the table the backend points the
  groups with and writes snapserver.conf's `source =` lines from at each start
  (`MULTIROOM_STREAM_SOURCES`);
* `rootfs/etc/asound.conf`, the writer's side, reinstalled by every OTA;
* `provisioning/snapcast.sh`, the snapserver.conf a card is flashed with, which
  runs inside a cloned pi-gen checkout and cannot import the table;
* pi-gen's `snd-aloop` options line, which numbers the two Loopback cards that
  asound.conf names by id and snapserver by index.

A slot two of them disagree on is a source that plays to nobody in every room,
with no error and every screen showing it playing.

The same heredoc writes the sections that open snapserver's control ports,
which the backend rewrites at each start from `SNAPSERVER_CONTROL_SECTIONS`.
"""
import re
from pathlib import Path

from backend.config.constants import MULTIROOM_STREAMS
from backend.core.models.audio_state import AudioSource
from backend.core.multiroom.snapcast import MULTIROOM_STREAM_SOURCES, SNAPSERVER_CONTROL_SECTIONS

ROOT = Path(__file__).resolve().parents[3]
ASOUND = ROOT / "rootfs" / "etc" / "asound.conf"
PROVISIONING = ROOT / "provisioning" / "snapcast.sh"
ALOOP = ROOT / "pi-gen" / "stage-milo" / "02-install-milo" / "01-run.sh"

_ALIAS = re.compile(r"^pcm\.(milo_\w+)_multiroom\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_CAPTURE_DEVICE = re.compile(r"^hw:(\d+),1,(\d+)$")


def _multiroom_aliases():
    """`{pcm: (card id, device, subdevice)}` for every `pcm.<pcm>_multiroom`."""
    aliases = {}
    for pcm, body in _ALIAS.findall(ASOUND.read_text(encoding="utf-8")):
        fields = dict(re.findall(r"^\s*(card|device|subdevice)\s+(\S+)\s*$", body, re.MULTILINE))
        aliases[pcm] = (fields.get("card"), fields.get("device"), fields.get("subdevice"))
    assert len(aliases) >= 10, f"only parsed {sorted(aliases)} from {ASOUND}"
    return aliases


def flashed_conf():
    """The snapserver.conf `provisioning/snapcast.sh` writes, as `tee` writes it.

    Read literally rather than run: the script builds an image.
    """
    script = PROVISIONING.read_text(encoding="utf-8")
    return script.split("<< 'EOF'\n", 1)[1].split("\nEOF\n", 1)[0] + "\n"


def conf_sections(conf):
    """`{section: {key: value}}` as snapserver reads a snapserver.conf: what
    follows a `#` is a comment."""
    sections, current = {}, None
    for line in conf.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


def _heredoc_sources():
    """The `source =` values the heredoc writes into snapserver.conf, in order."""
    sources = re.findall(r"^source\s*=\s*(\S+)\s*$", flashed_conf(), re.MULTILINE)
    assert sum(s.startswith("alsa:///") for s in sources) >= 10, f"only parsed {sources} from {PROVISIONING}"
    return sources


def _loopback_card_indexes():
    """`{card id: card index}` from the `options snd-aloop` line pi-gen writes."""
    line = re.search(r"options snd-aloop ([^\"\n]*)", ALOOP.read_text(encoding="utf-8")).group(1)
    options = dict(re.findall(r"(\w+)=(\S+)", line))
    cards = dict(zip(options["id"].split(","), options["index"].split(",")))
    assert len(cards) >= 2, f"only parsed {cards} from {ALOOP}"
    return cards


def test_every_multiroom_slot_is_captured_exactly_once():
    """Every source but NONE writes a substream of its own, on the card and the
    subdevice snapserver captures for it, and no slot is written nobody reads."""
    aliases = _multiroom_aliases()
    cards = _loopback_card_indexes()
    card_ids = {index: card for card, index in cards.items()}

    assert set(MULTIROOM_STREAMS) == set(AudioSource) - {AudioSource.NONE}
    assert set(aliases) == {stream.pcm for stream in MULTIROOM_STREAMS.values()}, (
        "a multiroom slot without a table entry is one snapserver never captures"
    )

    for source, stream in MULTIROOM_STREAMS.items():
        captured = _CAPTURE_DEVICE.match(stream.device)
        assert captured, f"{source.value}: {stream.device} is not a Loopback capture end"
        index, subdevice = captured.groups()
        assert aliases[stream.pcm] == (card_ids.get(index), "0", subdevice), (
            f"{source.value} writes {aliases[stream.pcm]}, snapserver reads {stream.device}"
        )

    slots = [(card, subdevice) for card, _, subdevice in aliases.values()]
    assert len(slots) == len(set(slots)), f"two sources share a substream: {sorted(slots)}"
    assert ("Loopback", "0") not in slots, "subdevice 0 of Loopback is CamillaDSP's input"


def test_the_provisioning_heredoc_is_the_capture_map():
    """A freshly flashed card starts with exactly the lines the backend would
    write, so its first boot rewrites nothing and restarts nothing."""
    assert _heredoc_sources() == list(MULTIROOM_STREAM_SOURCES)


def test_the_provisioning_heredoc_is_the_control_surface():
    """The sections that open a control port are the backend's whole, rewritten
    at each start: a card flashed with others restarts snapserver at its first
    boot, and one flashed with these rewrites nothing."""
    sections = conf_sections(flashed_conf())
    assert {"stream", "http", "tcp-control"} <= set(sections), f"only parsed {sorted(sections)} from {PROVISIONING}"

    for name, settings in SNAPSERVER_CONTROL_SECTIONS.items():
        assert sections[name] == dict(settings), f"[{name}] differs from SNAPSERVER_CONTROL_SECTIONS"
