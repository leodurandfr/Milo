"""Structural guardrail: every config that opens the HAT names it as the registry does.

The card is addressed by its ALSA id, never by its index (the kernel probes the
HAT and the two loopback cards in no fixed order), and that id is spelled in
six files across two deployment trees: CamillaDSP's playback device and
asound.conf on each role, and the default of each role's milo-alsa-passthrough.
`backend/hardware/registry.py::OUTPUT_CARD` is the spelling the backend checks
`/proc/asound/cards` against. One file drifting alone is a CamillaDSP that
cannot open its playback device, a mixer left attenuating, or a boot check that
blames a seated HAT — on a unit no test runner touches.

Each extractor reads the file's own syntax, and asserts it found something before
anything is compared, so a broken parse fails loudly instead of passing on an
empty surface.
"""
import re
from pathlib import Path

import yaml

from backend.hardware.registry import OUTPUT_CARD

REPO_ROOT = Path(__file__).resolve().parents[3]

CAMILLADSP_CONFIGS = (
    "rootfs/var/lib/milo/camilladsp/config.yml",
    "milo-client/configs/camilladsp/config.yml",
)
ASOUND_CONFS = (
    "rootfs/etc/asound.conf",
    "milo-client/rootfs/etc/asound.conf",
)
PASSTHROUGH_HELPERS = (
    "rootfs/usr/local/bin/milo-alsa-passthrough",
    "milo-client/rootfs/usr/local/bin/milo-alsa-passthrough",
)

HW_DEVICE_RE = re.compile(r"^(?:plug)?hw:([^,]+),")
# `card <id>` and `hw:<id>,` outside a comment. The loopback cards are the
# only other cards asound.conf addresses.
ASOUND_CARD_RE = re.compile(r"\bcard\s+(\w+)|\bhw:(\w+),")
PASSTHROUGH_DEFAULT_RE = re.compile(r'^CARD="\$\{1:-([^}]+)\}"', re.MULTILINE)


def _camilladsp_card(text: str) -> list[str]:
    device = yaml.safe_load(text)["devices"]["playback"]["device"]
    match = HW_DEVICE_RE.match(device)
    return [match.group(1)] if match else [device]


def _asound_cards(text: str) -> list[str]:
    cards = []
    for line in text.splitlines():
        for match in ASOUND_CARD_RE.finditer(line.split("#", 1)[0]):
            card = match.group(1) or match.group(2)
            if not card.startswith("Loopback"):
                cards.append(card)
    return cards


def _passthrough_default(text: str) -> list[str]:
    return PASSTHROUGH_DEFAULT_RE.findall(text)


EXTRACTORS = (
    *((path, _camilladsp_card) for path in CAMILLADSP_CONFIGS),
    *((path, _asound_cards) for path in ASOUND_CONFS),
    *((path, _passthrough_default) for path in PASSTHROUGH_HELPERS),
)


def output_card_names(root: Path) -> dict[str, list[str]]:
    """Every spelling of the output card, by file relative to `root`."""
    return {path: extract((root / path).read_text()) for path, extract in EXTRACTORS}


def test_every_config_names_the_card_the_registry_checks():
    found = output_card_names(REPO_ROOT)

    empty = [path for path, names in found.items() if not names]
    assert not empty, f"the extractor found no output card in {empty} — its syntax moved"
    total = sum(len(names) for names in found.values())
    assert total >= 6, f"only {total} occurrences found across {len(found)} files"

    drifted = {path: names for path, names in found.items() if set(names) != {OUTPUT_CARD}}
    assert not drifted, (
        f"these files name another output card than registry.OUTPUT_CARD "
        f"({OUTPUT_CARD!r}): {drifted}"
    )


# Where code opens a card: an ALSA device, a procfs card directory (`cards`, the
# list itself, is no card), or the card `amixer` reads. The captured token is
# the address, a variable included, so a bare index is visible as one.
CARD_ADDRESS_RE = re.compile(
    r"""\b(?:plug)?hw:([^,\s"'}]+),"""
    r"""|/proc/asound/card(?!s\b)([^/\s"'}]+)"""
    r"""|\bamixer\b[^|;)]*?\s["']?-c["',\s]+([^"'\s,\]]+)"""
)
CODE_GLOBS = (
    "backend/**/*.py",
    "milo-client/app/**/*.py",
    "rootfs/usr/local/bin/*",
    "rootfs/usr/local/lib/milo/*",
    "milo-client/rootfs/usr/local/bin/*",
    "milo-client/rootfs/usr/local/lib/milo/*",
)


def card_addresses(root: Path) -> list[tuple[str, int, str, str]]:
    """Every card address in shipped code under `root` — file, line, kind
    (`device`, `procfs`, `amixer`) and the address — comments and tests left out."""
    found = []
    for pattern in CODE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or "tests" in path.relative_to(root).parts:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for match in CARD_ADDRESS_RE.finditer(line.split("#", 1)[0]):
                    kind = ("device", "procfs", "amixer")[match.lastindex - 1]
                    found.append((str(path.relative_to(root)), number, kind, match[match.lastindex]))
    return found


def test_no_shipped_code_addresses_a_card_by_index():
    """snd-aloop reserves no slot, so the HAT and the two loopback cards take
    whichever indices their probes race into: `card 0` is the HAT on one boot
    and Loopback on the next. A diagnostic reading `amixer -c 0` then shows the
    loopback's controls in place of a HAT mixer drifted off unity."""
    found = card_addresses(REPO_ROOT)

    assert len(found) >= 10, f"only {len(found)} card addresses found — the pattern no longer matches"
    kinds = {kind for _, _, kind, _ in found}
    assert {"device", "amixer"} <= kinds, f"only {kinds} matched — a call shape is no longer read"

    numbered = [site for site in found if site[3].isdigit()]
    assert not numbered, f"these address a card by its index, which no boot fixes: {numbered}"
