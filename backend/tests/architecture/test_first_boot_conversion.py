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

from backend.tests.architecture.test_helper_deployment import REPO_ROOT, _shell_function, _uncommented

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


SERVER_HOSTNAME_SET = "hostnamectl set-hostname milo\n"
AS_MILO_GIT = r'runuser -u milo -- git -C "\$MILO_APP_DIR"'
CHECKOUT_CHOWN = re.compile(r'^\s*chown (?![^\n]*-h\b)[^\n]*"\$MILO_APP_DIR/([^"]+)"', re.MULTILINE)


def test_every_checkout_change_of_the_client_role_is_undone_by_the_revert():
    """`configure_server` hands back to milo whatever the client role took in the checkout.

    `_apply_client_filesystem` gives `milo-client/` to the milo-client user so
    the satellite can update itself, and every app update then rewrites
    `milo-client/app` through the repo link. A unit that reverts to a server
    keeps that subtree as it was left: owned by milo-client, and with
    `git status --porcelain` non-empty, which makes every Milō update answer
    "Local changes detected". The revert must give the subtree back to milo and
    reset it to what git tracks, as milo (a root git rewrites `.git/index` as
    root, and the backend's next checkout, as milo, fails on it), without `-x`,
    which would also wipe the prebuilt, ignored `milo-client/venv`. All of it
    before the hostname flips back to milo, so a revert cut by a power loss is
    retried in full, and inside one `if` condition: under `set -e` a bare
    failure would exit before the hostname and loop the revert on every boot.
    """
    functions = _functions(FIRST_BOOT.read_text())
    taken = CHECKOUT_CHOWN.findall(functions["_apply_client_filesystem"])
    assert taken, (
        "could not find the chown of the checkout in _apply_client_filesystem; the extractor is broken"
    )
    revert = functions["configure_server"]
    assert SERVER_HOSTNAME_SET in revert, "configure_server no longer sets the hostname back to milo"
    hostname_at = revert.index(SERVER_HOSTNAME_SET)

    for path in taken:
        chown = f'chown -R milo:milo "$MILO_APP_DIR/{path}"'
        assert chown in revert, f"configure_server never gives {path} back to milo"
        checkout = re.search(rf"{AS_MILO_GIT} checkout -- {re.escape(path)}(?=[\s;]|$)", revert)
        assert checkout, f"configure_server never restores the tracked content of {path}, as milo"
        clean = re.search(rf"{AS_MILO_GIT} clean -(\w+) -- {re.escape(path)}(?=[\s;]|$)", revert)
        assert clean, f"configure_server never removes what an app update added to {path}, as milo"
        flags = clean.group(1)
        assert {"f", "d"} <= set(flags), f"`git clean -{flags}` does not remove untracked directories"
        assert not {"x", "X"} & set(flags), f"`git clean -{flags}` also wipes ignored files, the prebuilt venv among them"
        guarded = [c for c in re.findall(r"^\s*if (.*?); then$", revert, re.MULTILINE | re.DOTALL) if chown in c]
        assert guarded and checkout.group(0) in guarded[0] and clean.group(0) in guarded[0], (
            f"the chown, checkout and clean of {path} must share one `if` condition, so a failure "
            "warns instead of exiting before the hostname"
        )
        assert revert.index(chown) < checkout.start() < clean.start() < hostname_at, (
            f"configure_server must chown {path}, then check it out, then clean it, before the hostname "
            "flips back to milo"
        )


CLIENT_USER_CREATED = re.compile(r'^\s*if ! id "\$MILO_CLIENT_USER"[^\n]*; then\n.*?^\s*fi$', re.MULTILINE | re.DOTALL)
GROUPS_ADDED = re.compile(r'^\s*usermod -aG (\S+) "\$MILO_CLIENT_USER"\s*$', re.MULTILINE)


def test_the_client_account_can_read_the_whole_journal():
    """The account the satellite app runs as is put in `adm`, even when it already exists.

    Outside `adm` an account reads only its own journal file, and journalctl
    still exits 0: `GET /diagnostic` then returns each unit's block without its
    systemd[1] lines (Started/Stopped/Failed — 89 lines of 204 measured on a
    satellite) and no kernel block at all, so a CamillaDSP stuck failing or a
    USB/DMA error no longer says why. The group must be added outside the
    `if ! id` guard, so a unit whose account was created before the change is
    corrected by the next conversion too.
    """
    body = _functions(FIRST_BOOT.read_text())["_apply_client_filesystem"]
    created = CLIENT_USER_CREATED.search(body)
    assert created, (
        "could not find the creation of $MILO_CLIENT_USER in _apply_client_filesystem; "
        "the extractor is broken"
    )
    outside = body[: created.start()] + body[created.end():]
    added = [groups.split(",") for groups in GROUPS_ADDED.findall(outside)]
    assert any("adm" in groups for groups in added), (
        "_apply_client_filesystem never adds $MILO_CLIENT_USER to adm outside its creation guard: "
        "the satellite's /diagnostic journal then misses every systemd and kernel line"
    )


CONVERSIONS = {
    "configure_client": "server",
    "resume_client_conversion": "server",
    "configure_client_with_hardware": "server",
    "configure_server": "client",
}


def test_every_role_change_clears_the_config_txt_blocks_of_the_role_left():
    """Each way into a role removes the other role's managed blocks from config.txt.

    Each apply helper removes only its own blocks, so a unit that ran the
    server wizard and is then adopted as a satellite kept the server's screen,
    IR and power LED blocks for good, and booted the server's card until a
    satellite apply swept that overlay line; the revert booted the satellite's
    card the same way. Fails when one of the three conversion paths, or the revert,
    reaches no `clear_role_blocks` of the role it leaves, or when that function
    stops handing its role to `remove_role_blocks`.
    """
    text = FIRST_BOOT.read_text()
    functions = _functions(text)
    assert "clear_role_blocks" in functions, "milo-first-boot defines no clear_role_blocks"
    assert re.search(r'^\s*remove_role_blocks "\$1"$', functions["clear_role_blocks"], re.MULTILINE), (
        "clear_role_blocks no longer hands its role to remove_role_blocks"
    )
    for entry, leaving in CONVERSIONS.items():
        reached = _reached(functions[entry], functions) | {entry}
        calls = [
            name for name in reached
            if re.search(rf"^\s*clear_role_blocks {leaving}$", functions[name], re.MULTILINE)
        ]
        assert calls, f"{entry} never clears the {leaving} role's blocks from config.txt"
