# backend/tests/architecture/test_multiroom_capture_map.py
"""Pin each source's multiroom slot to the capture snapserver reads it from.

In multiroom a source's unit writes `pcm.milo_<x>`, which asound.conf resolves to
`pcm.milo_<x>_multiroom`: one Loopback substream. snapserver captures the other
end of that substream as the source's named stream, and the backend points every
group at it (`Group.SetStream`). Five files declare a part of that chain, and
only one of them is Python:

* `MULTIROOM_STREAMS` (`config/constants.py`), the table the backend points the
  groups with and writes snapserver.conf's `source =` lines from at each start
  (`MULTIROOM_STREAM_SOURCES`);
* `rootfs/etc/asound.conf`, the writer's side, reinstalled by every OTA;
* `provisioning/snapcast.sh`, the snapserver.conf a card is flashed with, which
  runs inside a cloned pi-gen checkout and cannot import the table;
* the CamillaDSP configs, whose capture is subdevice 0 of a Loopback card;
* pi-gen's `snd-aloop` options line, which names the two Loopback cards.

A slot two of them disagree on is a source that plays to nobody in every room,
with no error and every screen showing it playing. Every one of them addresses
a card by its id: a card index is wherever the kernel happened to probe it, and
a slot the options line claims explicitly that another card already holds fails
that loopback card's registration outright.

The same heredoc writes the sections that open snapserver's control ports,
which the backend rewrites at each start from `SNAPSERVER_CONTROL_SECTIONS`.
"""
import re
from pathlib import Path

import yaml

from backend.config.constants import MULTIROOM_STREAMS
from backend.core.models.audio_state import AudioSource
from backend.core.multiroom.snapcast import MULTIROOM_STREAM_SOURCES, SNAPSERVER_CONTROL_SECTIONS

ROOT = Path(__file__).resolve().parents[3]
ASOUND = ROOT / "rootfs" / "etc" / "asound.conf"
CLIENT_ASOUND = ROOT / "milo-client" / "rootfs" / "etc" / "asound.conf"
PROVISIONING = ROOT / "provisioning" / "snapcast.sh"
ALOOP = ROOT / "pi-gen" / "stage-milo" / "02-install-milo" / "01-run.sh"
# (CamillaDSP config, asound.conf, the PCM that feeds CamillaDSP in that tree)
CAMILLADSP_INPUTS = (
    (ROOT / "rootfs" / "var" / "lib" / "milo" / "camilladsp" / "config.yml", ASOUND, "camilladsp"),
    (ROOT / "milo-client" / "configs" / "camilladsp" / "config.yml", CLIENT_ASOUND, "camilladsp_input"),
)

_PCM = re.compile(r"^pcm\.(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_CAPTURE_END = re.compile(r"^(?:plug)?hw:([A-Za-z]\w*),1,(\d+)$")


def _pcms(path):
    """`{pcm: (card, device, subdevice)}` for every hw-backed `pcm.<name>` of an asound.conf."""
    pcms = {}
    for pcm, body in _PCM.findall(path.read_text(encoding="utf-8")):
        fields = dict(re.findall(r"^\s*(card|device|subdevice)\s+(\S+)\s*$", body, re.MULTILINE))
        if "card" in fields:
            pcms[pcm] = (fields["card"], fields.get("device"), fields.get("subdevice"))
    assert len(pcms) >= 2, f"only parsed {sorted(pcms)} from {path}"
    return pcms


def _multiroom_aliases():
    """`{pcm: (card id, device, subdevice)}` for every `pcm.<pcm>_multiroom`."""
    aliases = {
        pcm.removesuffix("_multiroom"): slot
        for pcm, slot in _pcms(ASOUND).items()
        if pcm.endswith("_multiroom")
    }
    assert len(aliases) >= 10, f"only parsed {sorted(aliases)} from {ASOUND}"
    return aliases


def _capture_end(device, where):
    """`(card id, subdevice)` of a Loopback capture end, addressed by card id."""
    captured = _CAPTURE_END.match(device)
    assert captured, f"{where}: {device} is not a Loopback capture end named by card id"
    return captured.groups()


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


def _aloop_options():
    """`{option: value}` of the `options snd-aloop` line pi-gen writes."""
    line = re.search(r"options snd-aloop ([^\"\n]*)", ALOOP.read_text(encoding="utf-8")).group(1)
    options = dict(re.findall(r"(\w+)=(\S+)", line))
    assert len(options["id"].split(",")) >= 2, f"only parsed {options} from {ALOOP}"
    return options


def test_every_multiroom_slot_is_captured_exactly_once():
    """Every source but NONE writes a substream of its own, on the card and the
    subdevice snapserver captures for it, and no slot is written nobody reads."""
    aliases = _multiroom_aliases()

    assert set(MULTIROOM_STREAMS) == set(AudioSource) - {AudioSource.NONE}
    assert set(aliases) == {stream.pcm for stream in MULTIROOM_STREAMS.values()}, (
        "a multiroom slot without a table entry is one snapserver never captures"
    )

    for source, stream in MULTIROOM_STREAMS.items():
        card, subdevice = _capture_end(stream.device, source.value)
        assert aliases[stream.pcm] == (card, "0", subdevice), (
            f"{source.value} writes {aliases[stream.pcm]}, snapserver reads {stream.device}"
        )

    slots = [(card, subdevice) for card, _, subdevice in aliases.values()]
    assert len(slots) == len(set(slots)), f"two sources share a substream: {sorted(slots)}"


def test_camilladsp_captures_the_substream_its_tree_feeds_it():
    """On both roles CamillaDSP reads the capture end of the substream asound.conf
    sends it, and no source's multiroom slot is that substream: a source writing
    there would play through the server's speaker in direct mode's place."""
    slots = {(card, subdevice) for card, _, subdevice in _multiroom_aliases().values()}

    for config, asound, pcm in CAMILLADSP_INPUTS:
        device = yaml.safe_load(config.read_text(encoding="utf-8"))["devices"]["capture"]["device"]
        card, subdevice = _capture_end(device, config.name)
        assert _pcms(asound)[pcm] == (card, "0", subdevice), (
            f"{asound} feeds CamillaDSP through pcm.{pcm}, {config} captures {device}"
        )
        if asound == ASOUND:
            assert (card, subdevice) not in slots, f"a multiroom source writes CamillaDSP's input {device}"


def test_the_loopback_cards_are_named_and_never_numbered():
    """Every Loopback card the chain addresses is one the module creates under
    that id, and the module claims no card slot. An explicit `index=` that the
    HAT (or anything probed first) already holds makes snd-aloop's registration
    of that card fail with -EBUSY, and every stream on it dies in silence."""
    options = _aloop_options()
    ids = set(options["id"].split(","))

    assert "index" not in options, f"{ALOOP} claims card slots: index={options.get('index')}"

    addressed = {card for card, _, _ in _multiroom_aliases().values()}
    addressed |= {_capture_end(stream.device, stream.name)[0] for stream in MULTIROOM_STREAMS.values()}
    for _, asound, pcm in CAMILLADSP_INPUTS:
        addressed.add(_pcms(asound)[pcm][0])
    assert addressed <= ids, f"cards {sorted(addressed - ids)} are not snd-aloop ids {sorted(ids)}"
    assert ids <= addressed, f"snd-aloop creates {sorted(ids - addressed)}, which nothing reads"


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
