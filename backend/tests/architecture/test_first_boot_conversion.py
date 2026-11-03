"""Structural guardrail: the server-to-satellite conversion `milo-first-boot` runs.

A flashed card becomes a satellite on its next boot, in `milo-first-boot`, from
a marker the server's `POST /api/setup/become-client` left behind. That script
runs once, as root, on a unit nobody is logged into, and it reboots at the end:
a step it skips leaves no error anywhere, only a speaker that registers and
stays silent. CI cannot boot it, but every command in it is an absolute path,
so its order and its guards can be read from the source.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re

from tests.architecture.test_helper_deployment import REPO_ROOT, _shell_function, _uncommented

FIRST_BOOT = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-first-boot"
CLIENT_BIN = REPO_ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin"
APPLY_HARDWARE = "/usr/local/bin/milo-client-apply-hardware"
MARKER_REMOVAL = 'rm -f "$PENDING_CLIENT_ROLE_FILE"'


def test_wifi_adoption_applies_the_audio_overlay_before_reboot():
    """The WiFi adoption writes `hardware.json` and must also apply it.

    `milo-client-apply-hardware` is the only writer of the `MILO CLIENT AUDIO`
    block in `config.txt`. If the adoption only writes `hardware.json` and
    reboots, the satellite reports `hardware_configured`, the server never
    offers the configure step that runs the helper, and a card without an
    EEPROM gets no overlay: CamillaDSP targets a card that does not exist and
    the room stays silent. The marker may only go once the helper succeeded,
    so a failure exits without rebooting and the next boot retries the adoption.
    """
    body = _shell_function(FIRST_BOOT.read_text(), "configure_client_with_hardware")
    assert body and "_apply_client_filesystem" in body, (
        "could not extract configure_client_with_hardware from milo-first-boot; "
        "the extractor is broken"
    )
    code = _uncommented(body)

    call = re.search(rf"if ! {re.escape(APPLY_HARDWARE)} (\S+); then\n(.*?)\n\s*fi\n", code, re.DOTALL)
    assert call, f"configure_client_with_hardware does not run {APPLY_HARDWARE} under an `if !` guard"
    assert "exit 1" in call.group(2), "a failed overlay apply must exit 1, keeping the marker for a retry"

    helper = CLIENT_BIN / APPLY_HARDWARE.rsplit("/", 1)[1]
    assert helper.is_file(), f"{helper.name} is not shipped by milo-client/rootfs/usr/local/bin"
    flag = call.group(1)
    arms = re.findall(r"^\s*([-\w|]+)\)", helper.read_text(), re.MULTILINE)
    assert arms, f"could not read the argument cases of {helper.name}; the extractor is broken"
    assert any(flag in arm.split("|") for arm in arms), f"{helper.name} does not accept {flag}"

    assert MARKER_REMOVAL in code, "configure_client_with_hardware no longer removes the marker"
    assert "/sbin/reboot" in code, "configure_client_with_hardware no longer reboots"
    assert call.end() <= code.index(MARKER_REMOVAL) < code.index("/sbin/reboot"), (
        "the marker must be removed after the overlay is applied, and before the reboot"
    )
    early = re.findall(
        r"\b(?:remove|unlink)\(|\brm\b[^\n]*PENDING_CLIENT_ROLE_FILE", code[: call.start()]
    )
    assert not early, f"the marker is removed before the overlay is applied: {early}"
