"""Structural guardrail: the power button, as the backend stores it and as
`milo-apply-hardware` reads it.

The script is the only thing that turns a declared button into a bootloader
EEPROM setting and an LED block in `config.txt`, and it cannot import Python, so
it names the stored keys by string. `read_json` answers an empty string for a
missing key: a typo in `power_button.enabled` reads as "no button" (the board
keeps starting on power with the toggle showing one), and a typo in
`power_button.led_gpio_pin` fails the pin check and aborts the apply — or, if
the check went with it, writes `gpio==op,dl`, which drives nothing.

Every extractor asserts it found something first, so a broken parse fails
rather than passing on an empty surface.
"""
import re
from pathlib import Path

from backend.hardware.service import HardwareService

REPO_ROOT = Path(__file__).resolve().parents[3]
APPLY_SCRIPT = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-apply-hardware"


def _script() -> str:
    text = APPLY_SCRIPT.read_text()
    assert "apply_power_on_behaviour" in text, "the script no longer applies the EEPROM"
    return text


def _reads() -> dict:
    """{shell variable: hardware.json key} for every `VAR=$(read_json "key")`."""
    reads = dict(re.findall(r'^(\w+)=\$\(read_json "([^"]+)"\)$', _script(), re.MULTILINE))
    assert len(reads) >= 4, f"only {reads} extracted from milo-apply-hardware"
    return reads


def test_the_script_reads_the_keys_the_service_stores(tmp_path):
    """Resolved through the real service, not a dict typed here: each path the
    script asks for must exist in what `get_full_config` hands the routes."""
    power_reads = sorted(k for k in _reads().values() if k.startswith("power_button."))
    assert power_reads == ["power_button.enabled", "power_button.led_gpio_pin"]

    service = HardwareService()
    service.hardware_file = tmp_path / "hardware.json"
    config = service.get_full_config()
    for key in power_reads:
        node = config
        for part in key.split("."):
            assert part in node, f"{key} does not resolve in get_full_config()"
            node = node[part]


def test_the_led_line_drives_the_pin_read_from_hardware_json():
    """The pin in `config.txt` must be the stored one, not a constant left
    behind: the validator keeps the other peripherals off the stored pin."""
    variable = next(var for var, key in _reads().items() if key == "power_button.led_gpio_pin")
    assert f"gpio=${{{variable}}}=op,dl" in _script()
