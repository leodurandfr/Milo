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


HOSTNAME_SET = "hostnamectl set-hostname milo-client"
DEFAULT_HARDWARE = "_write_default_client_hardware"
HARDWARE_GUARD = '[[ -s "$MILO_CLIENT_DATA_DIR/hardware.json" ]] ||'


def _functions(text: str) -> dict[str, str]:
    names = re.findall(r"^(\w+)\(\) \{$", text, re.MULTILINE)
    bodies = {name: _uncommented(_shell_function(text, name)) for name in names}
    assert len(bodies) >= 3 and all(bodies.values()), (
        f"could not read the functions of milo-first-boot ({sorted(bodies)}); the extractor is broken"
    )
    return bodies


def _reached(code: str, functions: dict[str, str]) -> set[str]:
    """Every function `code` calls, directly or through another one."""
    reached: set[str] = set()
    pending = [code]
    while pending:
        body = pending.pop()
        for name, callee in functions.items():
            if name not in reached and re.search(rf"(?<![\w-]){name}(?![\w-])", body):
                reached.add(name)
                pending.append(callee)
    return reached


def test_an_interrupted_client_conversion_is_resumed():
    """A conversion cut by a power loss must finish on the next boot.

    `_apply_client_filesystem` sets the hostname first and locks the role
    (`setup_completed`) last, so a speaker unplugged during those 10-25 s boots
    back as hostname milo-client with setup not completed. If the branch that
    sees milo.local and that hostname does nothing, every later boot does
    nothing too: the speaker stays for good with server units still enabled, or
    no role at all, or a satellite whose data directory was never handed over.
    That branch is the conversion boot's only way back, so it must reach
    `_apply_client_filesystem` and the reboot, and keep a card already written
    to `hardware.json` rather than reset it to none.
    """
    text = FIRST_BOOT.read_text()
    functions = _functions(text)

    found = re.search(r"^    if \$milo_local_found; then\n(.*?)\n    else\n", text, re.MULTILINE | re.DOTALL)
    assert found, "could not find the milo.local-found branch of milo-first-boot; the extractor is broken"
    arms = re.search(
        r'if \[\[ "\$current_hostname" != "milo-client" \]\]; then\n(.*?)\n\s*else\n(.*?)\n\s*fi\b',
        found.group(1),
        re.DOTALL,
    )
    assert arms, "could not split the milo.local-found branch on the hostname; the extractor is broken"
    already_client = _uncommented(arms.group(2))

    reached = _reached(already_client, functions)
    assert "_apply_client_filesystem" in reached, (
        f"the already-client branch never reaches _apply_client_filesystem (it calls {sorted(reached)})"
    )
    assert any("/sbin/reboot" in functions[name] for name in reached), (
        "the already-client branch resumes the conversion but never reboots into the satellite"
    )

    for name in reached - {DEFAULT_HARDWARE}:
        assert not re.search(r">\s*\S*hardware\.json", functions[name]), (
            f"{name} writes hardware.json directly; the resume path may only write the default when it is missing"
        )
        for line in functions[name].splitlines():
            if DEFAULT_HARDWARE in line:
                assert line.strip().startswith(HARDWARE_GUARD), (
                    f"{name} resets hardware.json unconditionally: {line.strip()!r}"
                )


def test_the_client_hostname_marks_a_conversion_in_progress():
    """The client hostname is set before anything else the conversion changes.

    That hostname, without `setup_completed`, is what tells an interrupted
    conversion apart on the next boot: with milo.local answering it is resumed,
    without it `configure_server` reverts the unit. Setting it last, as
    `configure_server` does for its own hostname, leaves a unit cut after the
    server units were disabled named milo; with the server off at that boot it
    then stays "a server" with no backend, no wizard and no satellite, for good.
    """
    body = _functions(FIRST_BOOT.read_text())["_apply_client_filesystem"]
    assert HOSTNAME_SET in body, f"_apply_client_filesystem no longer runs `{HOSTNAME_SET}`"
    assert "systemctl disable" in body, "_apply_client_filesystem no longer disables the server units"
    assert body.index(HOSTNAME_SET) < body.index("systemctl disable"), (
        "the client hostname is set after the server units are disabled: an interrupted conversion "
        "with the server off is no longer recovered"
    )
